import base64
import io
import json
import re
import socket
import textwrap
import time
import logging
from contextlib import closing
from typing import Generator, List, Optional
from urllib.error import HTTPError
from urllib.parse import quote_plus, urljoin, urlparse
from urllib.request import Request, urlopen

from calibre import browser
from calibre.gui2 import open_url
from calibre.gui2.store import StorePlugin
from calibre.gui2.store.search_result import SearchResult
from calibre.gui2.store.web_store_dialog import WebStoreDialog
from calibre_plugins.store_annas_archive.constants import DEFAULT_TIMEOUT, MAX_PAGES_DEFAULT, TTLCache
from lxml import html
from lxml.etree import ParserError

try:
    from qt.core import QUrl
except ImportError:
    from PyQt5.Qt import QUrl

USER_AGENT = (
    'Mozilla/5.0 (X11; Linux x86_64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)

LIBGEN_MIRRORS = ['https://libgen.li', 'https://libgen.bz', 'https://libgen.vg']

_HEADERS = [
    ('User-Agent', USER_AGENT),
    ('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'),
    ('Accept-Language', 'en-US,en;q=0.9'),
    ('Connection', 'keep-alive'),
]

FORMATS      = ('EPUB', 'PDF', 'MOBI', 'AZW3', 'CBR', 'CBZ', 'FB2', 'DJVU', 'TXT')
_FORMATS     = frozenset(FORMATS)
_MD5_RE      = re.compile(r'^[0-9a-fA-F]{32}$')
_SIZE_RE     = re.compile(r'\b(\d+(?:\.\d+)?\s*(?:KB|MB|GB))\b', re.IGNORECASE)
_MD5_HREF_RE = re.compile(r'(?:[?&]md5=|/md5/|/book/)([a-fA-F0-9]{32})', re.IGNORECASE)
_GET_PHP_RE  = re.compile(r'get\.php\?md5=[0-9a-fA-F]{32}[^"\'<>\s]*')
_FILE_ID_RE  = re.compile(r'file\.php\?id=(\d+)')
_JUNK_RE     = re.compile(r'"\s*href="[^"]*"\s*>')

SearchResults = Generator[SearchResult, None, None]
logger = logging.getLogger(__name__)

_COVER_PALETTES = {
    'PDF':  ('#1a1a2e', '#e94560'),
    'EPUB': ('#0f3460', '#533483'),
    'MOBI': ('#2d6a4f', '#40916c'),
    'DJVU': ('#3d0c02', '#c64b00'),
    'FB2':  ('#1a3a1a', '#4a9e4a'),
}
_FONT_PATHS = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
]


def _clean(s):
    return re.sub(r'\s+', ' ', _JUNK_RE.sub(' ', s)).strip()

def _abs(base, href):
    return href if href.startswith('http') else urljoin(base + '/', href.lstrip('/'))

def _extract_md5(href):
    m = _MD5_HREF_RE.search(href)
    return m.group(1).lower() if m else None

def _is_html(resp):
    ct = resp.info().get_content_type() or ''
    return ct.lower().startswith(('text/html', 'application/xhtml+xml'))

def _mirror_ok(mirror, timeout=6):
    import http.client as _hc
    try:
        p   = urlparse(mirror)
        cls = _hc.HTTPSConnection if p.scheme == 'https' else _hc.HTTPConnection
        c   = cls(p.netloc, timeout=timeout)
        try:
            c.request('HEAD', '/', headers={'User-Agent': USER_AGENT})
            return c.getresponse().status < 500
        finally:
            c.close()
    except Exception:
        return False

def _make_cover(title, author, fmt='', size=''):
    try:
        from PIL import Image, ImageDraw, ImageFont
        import os
        W, H     = 200, 280
        bg, acc  = _COVER_PALETTES.get(fmt.upper(), ('#1a1a2e', '#e94560'))
        img      = Image.new('RGB', (W, H), bg)
        draw     = ImageDraw.Draw(img)
        draw.rectangle([0, 0, W, 8], fill=acc)
        draw.rectangle([0, H - 8, W, H], fill=acc)
        try:
            bold_path = next(p for p in _FONT_PATHS if os.path.exists(p))
            fnt_b = ImageFont.truetype(bold_path, 13)
            fnt_r = ImageFont.truetype(bold_path.replace('-Bold', ''), 10)
            fnt_s = ImageFont.truetype(bold_path, 11)
        except Exception:
            fnt_b = fnt_r = fnt_s = ImageFont.load_default()
        if fmt:
            draw.rectangle([10, 16, 52, 36], fill=acc)
            draw.text((15, 19), fmt.upper()[:4], fill='#ffffff', font=fnt_s)
        if size:
            draw.text((W - 55, 19), size, fill=acc, font=fnt_s)
        y = 50
        for line in textwrap.wrap(title, 20)[:6]:
            draw.text((10, y), line, fill='#ffffff', font=fnt_b)
            y += 18
        draw.rectangle([10, y + 4, W - 10, y + 6], fill=acc)
        y += 14
        for line in textwrap.wrap(author, 26)[:3]:
            draw.text((10, y), line, fill='#aaaaaa', font=fnt_r)
            y += 13
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=80)
        return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ''

def _row_title(cell):
    for a in cell.xpath('.//a[contains(@href,"edition.php")]'):
        t = _clean(a.text_content())
        if len(t) >= 3:
            return t
    for a in cell.xpath('.//a'):
        t = _clean(a.text_content())
        if len(t) >= 3:
            return t
    return _clean(cell.text_content())[:200]

def _parse_results(raw, base):
    results, seen = [], set()
    try:
        doc = html.fromstring(raw)
    except (ParserError, Exception):
        return results

    rows = doc.xpath('//table[@id="tablelibgen"]//tr[count(td)>=8]')
    if not rows:
        rows = doc.xpath('//tr[count(td)>=4 and .//a[contains(@href,"md5")]]')

    for row in rows:
        try:
            cells   = row.xpath('td')
            if len(cells) < 8:
                continue
            mirrors = cells[8] if len(cells) >= 9 else cells[-1]

            md5, ads_url = None, None
            for h in mirrors.xpath('.//a/@href'):
                if not md5:
                    md5 = _extract_md5(h)
                if not ads_url and 'ads.php' in h:
                    ads_url = _abs(base, h)
            if not md5:
                for h in row.xpath('.//a/@href'):
                    md5 = _extract_md5(h)
                    if md5:
                        break
            if not md5 or md5 in seen:
                continue
            seen.add(md5)

            title = _row_title(cells[0])
            if not title:
                continue
            author  = _clean(cells[1].text_content()) if len(cells) > 1 else ''
            ext     = cells[7].text_content().strip().upper() if len(cells) > 7 else ''
            if ext not in _FORMATS:
                ext = ''
            size_tx = cells[6].text_content().strip() if len(cells) > 6 else ''
            m_sz    = _SIZE_RE.search(size_tx)
            size    = m_sz.group(1) if m_sz else ''

            file_id = edition_id = None
            isbn    = ''
            for h in cells[6].xpath('.//a/@href'):
                m = _FILE_ID_RE.search(h)
                if m:
                    file_id = m.group(1)
                    break
            for h in cells[0].xpath('.//a/@href'):
                m = re.search(r'edition\.php\?id=(\d+)', h)
                if m:
                    edition_id = m.group(1)
                    break
            isbn_raw = cells[0].xpath('.//font[@color="green"]/text()')
            if isbn_raw:
                parts = [x.strip() for x in isbn_raw[0].split(';') if x.strip()]
                isbn  = next((i for i in parts if len(i) == 13), parts[0] if parts else '')

            year = _clean(cells[3].text_content()) if len(cells) > 3 else ''
            lang = _clean(cells[4].text_content()) if len(cells) > 4 else ''

            cover = _make_cover(title.split('[')[0].strip(), author, ext, size) or None

            s             = SearchResult()
            s.detail_item = md5
            s.title       = '{} [{}]'.format(title, size) if size else title
            s.author      = author
            s.formats     = ext
            s.cover_url   = cover
            s.price       = '$0.00'
            s.drm         = SearchResult.DRM_UNLOCKED
            s._file_id    = file_id or ''
            s._edition_id = edition_id or ''
            s._isbn       = isbn
            s._ads_url    = ads_url or ''
            s._year       = year
            s._lang       = lang
            results.append(s)
        except Exception as exc:
            logger.debug('Row parse error: %s', exc)

    return results

def _get_candidates(raw, base):
    direct, books, ipfs = [], [], []

    def add(lst, url):
        if url and url not in lst:
            lst.append(url)

    text = raw.decode('utf-8', 'replace').replace('&amp;', '&')
    try:
        doc = html.fromstring(raw)
    except Exception:
        doc = None

    if doc is not None:
        for h in doc.xpath('//a[@href]/@href'):
            h = h.replace('&amp;', '&')
            if _FILE_ID_RE.search(h):
                add(direct, _abs(base, h))
            elif 'get.php' in h:
                add(direct, _abs(base, h))
            elif re.search(r'cloudflare-ipfs\.com/ipfs/', h, re.I):
                add(ipfs, h)
            elif re.search(r'/book/[0-9a-fA-F]{32}', h, re.I):
                add(books, _abs(base, h))

    for m in _GET_PHP_RE.finditer(text):
        add(direct, _abs(base, m.group(0)))

    return direct + ipfs, books


class AnnasArchiveStore(StorePlugin):

    def __init__(self, gui, name, config=None, base_plugin=None):
        super().__init__(gui, name, config, base_plugin)
        self._cache  = TTLCache(ttl=300)
        self._mirror = LIBGEN_MIRRORS[0]

    def _pick_mirror(self):
        for m in LIBGEN_MIRRORS:
            try:
                socket.gethostbyname(urlparse(m).netloc)
                if _mirror_ok(m):
                    self._mirror = m
                    return m
            except Exception:
                pass
        return LIBGEN_MIRRORS[0]

    def _search_url(self, query, mirror, page=1):
        url = (
            '{}/index.php?req={}'
            '&columns%5B%5D=t&columns%5B%5D=a&columns%5B%5D=s'
            '&columns%5B%5D=y&columns%5B%5D=p&columns%5B%5D=i'
            '&objects%5B%5D=f&objects%5B%5D=e&objects%5B%5D=s'
            '&objects%5B%5D=a&objects%5B%5D=p&objects%5B%5D=w'
            '&topics%5B%5D=l&topics%5B%5D=c&topics%5B%5D=f'
            '&topics%5B%5D=a&topics%5B%5D=m&topics%5B%5D=r&topics%5B%5D=s'
            '&res=25&filesuns=all'
        ).format(mirror, quote_plus(query))
        if page > 1:
            url += '&page={}'.format(page)
        return url

    def _search(self, query, max_results, timeout):
        if not query.strip():
            return
        mirror    = self._pick_mirror()
        br        = browser()
        br.addheaders = list(_HEADERS)
        count     = 0
        page      = 1
        max_pages = (self.config or {}).get('max_pages', MAX_PAGES_DEFAULT)

        while count < max_results and page <= max_pages:
            try:
                with closing(br.open(self._search_url(query, mirror, page), timeout=timeout)) as r:
                    raw = r.read()
            except HTTPError:
                remaining = [m for m in LIBGEN_MIRRORS if m != mirror]
                if remaining:
                    mirror = remaining[0]
                    continue
                break
            except Exception:
                break

            page_results = _parse_results(raw, mirror)
            if not page_results:
                break
            for r in page_results:
                if count >= max_results:
                    break
                yield r
                count += 1
            if len(page_results) < 20:
                break
            page += 1
            if count < max_results and page <= max_pages:
                time.sleep(0.5)

    def search(self, query, max_results=10, timeout=DEFAULT_TIMEOUT):
        timeout   = (self.config or {}).get('timeout', timeout)
        cache_key = '{}|{}'.format(query, max_results)
        cached    = self._cache.get(cache_key)
        if cached is not None:
            yield from cached
            return
        results = []
        try:
            for r in self._search(query, max_results, timeout):
                results.append(r)
                yield r
        except Exception as exc:
            logger.exception('Search error: %s', exc)
        if results:
            self._cache.set(cache_key, results)

    def open(self, parent=None, detail_item=None, external=False):
        mirror = self._pick_mirror()
        url    = ('{}/file.php?md5={}'.format(mirror, detail_item)
                  if detail_item and _MD5_RE.match(detail_item)
                  else mirror)
        if external or (self.config or {}).get('open_external', False):
            open_url(QUrl(url))
        else:
            d = WebStoreDialog(self.gui, mirror, parent, url)
            d.setWindowTitle(self.name)
            d.set_tags((self.config or {}).get('tags', ''))
            d.exec()

    def get_details(self, search_result, timeout=15):
        if not search_result.detail_item:
            return
        md5        = search_result.detail_item
        fmt        = (search_result.formats or '').split(',')[0].strip().lower()
        if fmt not in {f.lower() for f in FORMATS}:
            fmt = 'epub'
        file_id    = getattr(search_result, '_file_id', '')
        edition_id = getattr(search_result, '_edition_id', '')
        isbn       = getattr(search_result, '_isbn', '')
        lang       = getattr(search_result, '_lang', '')

        if edition_id:
            try:
                req = Request('https://libgen.li/json.php?object=e&addkeys=*&ids={}'.format(edition_id))
                req.add_header('User-Agent', USER_AGENT)
                with urlopen(req, timeout=timeout) as r:
                    ed = json.loads(r.read()).get(edition_id, {})
                if ed.get('title'):
                    search_result.title = ed['title']
                if ed.get('author'):
                    search_result.author = ed['author']
                if ed.get('publisher'):
                    search_result.publisher = ed['publisher']
                if ed.get('year'):
                    search_result.pubdate = str(ed['year'])
                extras = []
                for label, key in [('Series', 'series_name'), ('Pages', 'pages'),
                                    ('DOI', 'doi')]:
                    if ed.get(key):
                        extras.append('{}: {}'.format(label, ed[key]))
                if isbn:
                    extras.append('ISBN: {}'.format(isbn))
                if lang:
                    extras.append('Language: {}'.format(lang))
                if extras:
                    search_result.comments = '\n'.join(extras)
            except Exception as exc:
                logger.debug('Edition metadata failed: %s', exc)

        if file_id:
            try:
                req2 = Request('https://libgen.xyz/api/search/by-id?id={}'.format(file_id))
                req2.add_header('User-Agent', USER_AGENT)
                req2.add_header('Accept', 'application/json')
                with urlopen(req2, timeout=timeout) as r2:
                    meta = json.loads(r2.read()).get('result', {})
                if meta.get('description'):
                    desc     = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', meta['description'])).strip()
                    existing = search_result.comments or ''
                    search_result.comments = (existing + '\n\n' + desc).strip()
                if not search_result.pubdate and meta.get('year'):
                    search_result.pubdate = str(meta['year'])
            except Exception as exc:
                logger.debug('Nuxt metadata failed: %s', exc)

        if file_id:
            try:
                req3 = Request('https://libgen.li/json.php?object=f&addkeys=*&ids={}'.format(file_id))
                req3.add_header('User-Agent', USER_AGENT)
                with urlopen(req3, timeout=timeout) as r3:
                    fdata = json.loads(r3.read()).get(file_id, {})
                if fdata.get('libgen_id') and fdata.get('cover_exists') == '1':
                    bucket = (int(fdata['libgen_id']) // 1000) * 1000
                    search_result.cover_url = 'https://libgen.li/covers/{}/{}_small.jpg'.format(bucket, md5)
            except Exception as exc:
                logger.debug('Cover bucket lookup failed: %s', exc)

        if file_id:
            search_result.downloads['Libgen.direct.{}'.format(fmt)] = \
                'https://libgen.download/api/download?id={}'.format(file_id)

        for i, mirror in enumerate(LIBGEN_MIRRORS, 1):
            try:
                socket.gethostbyname(urlparse(mirror).netloc)
            except Exception:
                continue
            if not _mirror_ok(mirror, timeout=6):
                continue
            search_result.downloads['Libgen.{}.{}'.format(i, fmt)] = \
                '{}/ads.php?md5={}'.format(mirror, md5)

    def create_browser(self):
        br   = browser()
        br.addheaders = list(_HEADERS)
        orig = br.open

        def intercept(url_or_req, *args, **kwargs):
            url = (url_or_req if isinstance(url_or_req, str)
                   else url_or_req.get_full_url())

            if re.search(r'file\.php\?id=\d+', url):
                return orig(url_or_req, *args, **kwargs)
            if 'libgen.download' in url and '/api/download' in url:
                return orig(url_or_req, *args, **kwargs)
            if not (('libgen' in url) and ('ads.php' in url or 'file.php' in url)):
                return orig(url_or_req, *args, **kwargs)

            resp = orig(url_or_req, *args, **kwargs)
            if not _is_html(resp):
                return resp

            raw = resp.read()
            try:
                resp.close()
            except Exception:
                pass

            parsed  = urlparse(url)
            base    = '{}://{}'.format(parsed.scheme, parsed.netloc)
            timeout = kwargs.get('timeout', 90)
            direct, _ = _get_candidates(raw, base)

            for cand in direct:
                try:
                    r = orig(cand, timeout=timeout)
                    if not _is_html(r):
                        return r
                    try:
                        r.close()
                    except Exception:
                        pass
                except Exception as exc:
                    logger.warning('Candidate failed %s: %s', cand, exc)
                time.sleep(0.3)

            md5_m = re.search(r'[?&]md5=([a-fA-F0-9]{32})', url)
            if md5_m and 'ads.php' in url:
                md5 = md5_m.group(1)
                for retry in LIBGEN_MIRRORS:
                    if urlparse(retry).netloc == parsed.netloc:
                        continue
                    try:
                        rr = orig('{}/ads.php?md5={}'.format(retry, md5), timeout=timeout)
                        if not _is_html(rr):
                            return rr
                        rt = rr.read().decode('utf-8', 'replace').replace('&amp;', '&')
                        try:
                            rr.close()
                        except Exception:
                            pass
                        rb = '{}://{}'.format(urlparse(retry).scheme, urlparse(retry).netloc)
                        for m in _GET_PHP_RE.finditer(rt):
                            try:
                                r = orig(_abs(rb, m.group(0)), timeout=timeout)
                                if not _is_html(r):
                                    return r
                                try:
                                    r.close()
                                except Exception:
                                    pass
                            except Exception:
                                pass
                    except Exception:
                        pass
                    time.sleep(1)

            raise IOError('No working download link for {}'.format(url))

        br.open = intercept
        return br

    def config_widget(self):
        from calibre_plugins.store_annas_archive.config import ConfigWidget
        return ConfigWidget(self)

    def save_settings(self, config_widget):
        config_widget.save_settings()
