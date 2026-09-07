"""
Libgen Metadata Source Plugin for Calibre.

Fetches metadata from:
  - libgen.li/json.php?object=e  (title, author, publisher, year, pages, doi, series)
  - libgen.xyz/api/search/by-id  (description, language)
  - libgen.li/covers/<bucket>/<md5>_small.jpg  (cover)

Searches by:
  - ISBN  -> libgen.li/index.php?req=isbn:XXXXXXXXXX
  - Title/Author -> libgen.li/index.php?req=...
"""

import re
import json
import logging
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from threading import Thread

from calibre.ebooks.metadata.book.base import Metadata
from calibre.ebooks.metadata.sources.base import Source

logger = logging.getLogger(__name__)

USER_AGENT = (
    'Mozilla/5.0 (X11; Linux x86_64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)

def _get(url, timeout=15):
    req = Request(url)
    req.add_header('User-Agent', USER_AGENT)
    req.add_header('Accept', 'application/json, text/html, */*')
    with urlopen(req, timeout=timeout) as r:
        return r.read()

def _strip_html(s):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', s or '')).strip()

def _search_libgen(query, max_results=5):
    """
    Search libgen.li and return list of dicts with file_id, edition_id, md5, title, author.
    """
    from lxml import html as lxml_html
    import socket
    from urllib.parse import urljoin

    mirrors = ['https://libgen.li', 'https://libgen.bz', 'https://libgen.vg']
    mirror = mirrors[0]
    for m in mirrors:
        try:
            socket.gethostbyname(m.split('//')[1])
            mirror = m
            break
        except Exception:
            continue

    url = (
        '{}/index.php?req={}'
        '&columns%5B%5D=t&columns%5B%5D=a&columns%5B%5D=s'
        '&columns%5B%5D=y&columns%5B%5D=p&columns%5B%5D=i'
        '&objects%5B%5D=f&objects%5B%5D=e'
        '&topics%5B%5D=l&topics%5B%5D=f'
        '&res=10&filesuns=all'
    ).format(mirror, quote_plus(query))

    try:
        raw  = _get(url)
        doc  = lxml_html.fromstring(raw)
    except Exception as exc:
        logger.warning('Libgen search failed: %s', exc)
        return []

    results = []
    rows = doc.xpath('//table[@id="tablelibgen"]//tr[count(td)>=8]')
    for row in rows[:max_results]:
        try:
            cells = row.xpath('td')
            # MD5
            md5 = None
            for h in cells[-1].xpath('.//a/@href'):
                m = re.search(r'[?&]md5=([a-fA-F0-9]{32})', h, re.I)
                if m:
                    md5 = m.group(1).lower()
                    break
            if not md5:
                continue
            # file_id
            file_id = None
            for h in cells[6].xpath('.//a/@href'):
                m = re.search(r'file\.php\?id=(\d+)', h)
                if m:
                    file_id = m.group(1)
                    break
            # edition_id
            edition_id = None
            for h in cells[0].xpath('.//a/@href'):
                m = re.search(r'edition\.php\?id=(\d+)', h)
                if m:
                    edition_id = m.group(1)
                    break
            # isbn
            isbn = ''
            isbn_raw = cells[0].xpath('.//font[@color="green"]/text()')
            if isbn_raw:
                parts = [x.strip() for x in isbn_raw[0].split(';') if x.strip()]
                isbn = next((i for i in parts if len(i) == 13), parts[0] if parts else '')

            title  = re.sub(r'\s+', ' ', cells[0].text_content()).strip()[:200]
            author = re.sub(r'\s+', ' ', cells[1].text_content()).strip() if len(cells) > 1 else ''
            results.append(dict(
                md5=md5, file_id=file_id, edition_id=edition_id,
                isbn=isbn, title=title, author=author
            ))
        except Exception:
            continue
    return results


def _fetch_metadata(hit):
    """Fetch full metadata for one search hit. Returns a Metadata object."""
    mi = Metadata(hit['title'], [hit['author']] if hit['author'] else [])
    mi.set_identifier('md5', hit['md5'])
    if hit.get('isbn'):
        mi.isbn = hit['isbn']

    # Edition record: title, author, publisher, year, pages, doi, series
    if hit.get('edition_id'):
        try:
            data = json.loads(_get(
                'https://libgen.li/json.php?object=e&addkeys=*&ids={}'.format(
                    hit['edition_id'])
            )).get(hit['edition_id'], {})
            if data.get('title'):
                mi.title = data['title']
            if data.get('author'):
                mi.authors = [a.strip() for a in data['author'].split(';') if a.strip()]
            if data.get('publisher'):
                mi.publisher = data['publisher']
            if data.get('year'):
                try:
                    from datetime import datetime
                    mi.pubdate = datetime(int(data['year']), 1, 1)
                except Exception:
                    pass
            if data.get('series_name'):
                mi.series = data['series_name']
            if data.get('doi'):
                mi.set_identifier('doi', data['doi'])
        except Exception as exc:
            logger.debug('Edition fetch failed: %s', exc)

    # File record: cover bucket
    cover_url = None
    if hit.get('file_id'):
        try:
            fdata = json.loads(_get(
                'https://libgen.li/json.php?object=f&addkeys=*&ids={}'.format(
                    hit['file_id'])
            )).get(hit['file_id'], {})
            if fdata.get('libgen_id') and fdata.get('cover_exists') == '1':
                bucket = (int(fdata['libgen_id']) // 1000) * 1000
                cover_url = 'https://libgen.li/covers/{}/{}_small.jpg'.format(
                    bucket, hit['md5'])
        except Exception as exc:
            logger.debug('File record fetch failed: %s', exc)

        # Nuxt API: description + language
        try:
            meta = json.loads(_get(
                'https://libgen.xyz/api/search/by-id?id={}'.format(hit['file_id'])
            )).get('result', {})
            if meta.get('description'):
                mi.comments = _strip_html(meta['description'])
            if meta.get('language'):
                mi.language = meta['language']
        except Exception as exc:
            logger.debug('Nuxt metadata failed: %s', exc)

    if cover_url:
        mi.has_cover = True
        mi.set_identifier('libgen_cover', cover_url)

    mi.source_relevance = 0
    return mi


class LibgenMetadata(Source):
    name                    = 'Libgen'
    description             = 'Downloads metadata and covers from Library Genesis'
    author                  = 'cal-annas plugin'
    version                 = (1, 0, 0)
    minimum_calibre_version = (5, 0, 0)

    capabilities   = frozenset({'identify', 'cover'})
    touched_fields = frozenset({
        'title', 'authors', 'publisher', 'pubdate',
        'comments', 'series', 'language',
        'identifier:isbn', 'identifier:md5', 'identifier:doi',
    })
    has_html_comments          = False
    cached_cover_url_is_reliable = True

    def get_book_url(self, identifiers):
        md5 = identifiers.get('md5')
        if md5:
            return ('md5', md5, 'https://libgen.li/file.php?md5={}'.format(md5))

    def get_cached_cover_url(self, identifiers):
        md5 = identifiers.get('md5')
        if md5:
            return self.cached_identifier_to_cover_url(md5)

    def identify(self, log, result_queue, abort, title=None, authors=None,
                 identifiers={}, timeout=30):
        # Build query: prefer ISBN, then title+author
        isbn = identifiers.get('isbn') or identifiers.get('ISBN')
        md5  = identifiers.get('md5')
        if isbn:
            query = 'isbn:{}'.format(isbn)
        elif md5:
            query = md5
        elif title:
            query = title
            if authors:
                query += ' ' + authors[0]
        else:
            return

        try:
            hits = _search_libgen(query, max_results=5)
        except Exception as exc:
            log.exception('Search failed: %s', exc)
            return

        threads = []
        results = []

        def worker(hit):
            try:
                mi = _fetch_metadata(hit)
                results.append(mi)
                # Cache cover URL
                cover_url = mi.identifiers.get('libgen_cover')
                if cover_url:
                    self.cache_identifier_to_cover_url(hit['md5'], cover_url)
            except Exception as exc:
                log.warning('Metadata fetch failed for %s: %s', hit.get('md5'), exc)

        for hit in hits:
            if abort.is_set():
                return
            t = Thread(target=worker, args=(hit,))
            t.daemon = True
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout)

        for mi in results:
            result_queue.put(mi)

    def download_cover(self, log, result_queue, abort, title=None, authors=None,
                       identifiers={}, timeout=30, get_best_cover=False):
        md5 = identifiers.get('md5')
        url = self.get_cached_cover_url(identifiers)
        if not url and md5:
            # Try to find it via file record
            try:
                hits = _search_libgen(title or md5, max_results=3)
                for hit in hits:
                    if hit['md5'] == md5:
                        fdata = json.loads(_get(
                            'https://libgen.li/json.php?object=f&addkeys=*&ids={}'.format(
                                hit['file_id'])
                        )).get(hit['file_id'], {})
                        if fdata.get('libgen_id') and fdata.get('cover_exists') == '1':
                            bucket = (int(fdata['libgen_id']) // 1000) * 1000
                            url = 'https://libgen.li/covers/{}/{}_small.jpg'.format(
                                bucket, md5)
                        break
            except Exception as exc:
                log.warning('Cover URL lookup failed: %s', exc)

        if url:
            self.download_image(url, timeout, log, result_queue)
