import re
import json
import logging
from urllib.request import Request, urlopen
from urllib.parse import quote_plus
from datetime import datetime

from calibre.customize import FileTypePlugin

logger   = logging.getLogger(__name__)
UA       = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
_ID_RE   = re.compile(r'\[id:(\d+)\]')
_SIZE_RE = re.compile(r'\s*\[\d+(?:\.\d+)?\s*(?:KB|MB|GB)\]', re.I)


def _get(url, timeout=15):
    req = Request(url)
    req.add_header('User-Agent', UA)
    req.add_header('Accept', 'application/json, */*')
    with urlopen(req, timeout=timeout) as r:
        return r.read()


def _strip_html(s):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', s or '')).strip()


def _search_libgen(query):
    from lxml import html as lxml_html
    url = ('https://libgen.li/index.php?req={}'
           '&objects%5B%5D=f&objects%5B%5D=e'
           '&topics%5B%5D=l&topics%5B%5D=f&res=5&filesuns=all'
           ).format(quote_plus(query))
    doc  = lxml_html.fromstring(_get(url, timeout=20))
    rows = doc.xpath('//table[@id="tablelibgen"]//tr[count(td)>=8]')
    for row in rows[:1]:
        cells = row.xpath('td')
        md5   = next((re.search(r'[?&]md5=([a-fA-F0-9]{32})', h, re.I).group(1).lower()
                      for h in cells[-1].xpath('.//a/@href')
                      if re.search(r'[?&]md5=([a-fA-F0-9]{32})', h, re.I)), None)
        if not md5:
            continue
        file_id = next((re.search(r'file\.php\?id=(\d+)', h).group(1)
                        for h in cells[6].xpath('.//a/@href')
                        if re.search(r'file\.php\?id=(\d+)', h)), None)
        ed_id   = next((re.search(r'edition\.php\?id=(\d+)', h).group(1)
                        for h in cells[0].xpath('.//a/@href')
                        if re.search(r'edition\.php\?id=(\d+)', h)), None)
        parts   = ([x.strip() for x in cells[0].xpath('.//font[@color="green"]/text()')[0].split(';')
                    if x.strip()] if cells[0].xpath('.//font[@color="green"]/text()') else [])
        isbn    = next((i for i in parts if len(i) == 13), parts[0] if parts else '')
        return dict(md5=md5, file_id=file_id, edition_id=ed_id, isbn=isbn)
    return None


class LibgenPostImport(FileTypePlugin):
    name                    = 'Libgen Auto Metadata'
    description             = "Fetches metadata from Libgen for EPUBs added via the store plugin."
    author                  = 'a-peirogon'
    version                 = (0, 4, 0)
    minimum_calibre_version = (5, 0, 0)
    supported_platforms     = ['windows', 'osx', 'linux']
    file_types              = {'epub'}
    on_import               = False
    on_postimport           = True

    def postimport(self, book_id, book_format, db):
        try:
            self._run(book_id, db)
        except Exception as exc:
            logger.warning('LibgenPostImport failed for book %s: %s', book_id, exc)

    def _run(self, book_id, db):
        title  = db.field_for('title', book_id) or ''
        m_id   = _ID_RE.search(title)
        file_id   = m_id.group(1) if m_id else None
        edition_id = None
        md5 = isbn = None

        clean = _ID_RE.sub('', _SIZE_RE.sub('', title)).strip()

        if not file_id:
            authors = db.field_for('authors', book_id) or []
            author  = authors[0] if authors else ''
            query   = clean
            if author and 'Desconocido' not in author and 'Unknown' not in author:
                query += ' ' + author
            try:
                hit = _search_libgen(query.strip())
                if hit:
                    file_id    = hit['file_id']
                    edition_id = hit['edition_id']
                    md5        = hit['md5']
                    isbn       = hit['isbn']
            except Exception as exc:
                logger.debug('Libgen search failed: %s', exc)

        if not file_id:
            if clean != title:
                try:
                    db.set_field('title', {book_id: clean})
                except Exception:
                    pass
            return

        fields, comments = {}, []

        if edition_id:
            try:
                ed = json.loads(_get(
                    'https://libgen.li/json.php?object=e&addkeys=*&ids={}'.format(edition_id)
                )).get(edition_id, {})
                if ed.get('title'):
                    fields['title'] = ed['title']
                if ed.get('author'):
                    fields['authors'] = [a.strip() for a in ed['author'].split(';') if a.strip()]
                if ed.get('publisher'):
                    fields['publisher'] = ed['publisher']
                if ed.get('year'):
                    try:
                        fields['pubdate'] = datetime(int(ed['year']), 1, 2)
                    except Exception:
                        pass
                if ed.get('series_name'):
                    fields['series'] = ed['series_name']
                if ed.get('doi'):
                    self._set_id(db, book_id, 'doi', ed['doi'])
                if ed.get('pages'):
                    comments.append('Pages: {}'.format(ed['pages']))
            except Exception as exc:
                logger.debug('Edition fetch failed: %s', exc)
        elif clean:
            fields['title'] = clean

        try:
            meta = json.loads(_get(
                'https://libgen.xyz/api/search/by-id?id={}'.format(file_id)
            )).get('result', {})
            if meta.get('description'):
                comments.append(_strip_html(meta['description']))
            if meta.get('language'):
                fields['languages'] = [meta['language']]
            if 'title'   not in fields and meta.get('title'):
                fields['title'] = meta['title']
            if 'authors' not in fields and meta.get('author'):
                au = meta['author']
                fields['authors'] = au if isinstance(au, list) else [au]
            if 'pubdate' not in fields and meta.get('year'):
                try:
                    fields['pubdate'] = datetime(int(meta['year']), 1, 2)
                except Exception:
                    pass
        except Exception as exc:
            logger.debug('Nuxt metadata failed: %s', exc)

        if comments:
            fields['comments'] = '\n\n'.join(comments)
        if md5:
            self._set_id(db, book_id, 'md5', md5)
        if isbn:
            self._set_id(db, book_id, 'isbn', isbn)

        try:
            cover = self._cover(file_id, md5)
            if cover:
                db.set_cover({book_id: cover})
        except Exception:
            pass

        for field, value in fields.items():
            try:
                db.set_field(field, {book_id: value})
            except Exception as exc:
                logger.debug('set_field %s failed: %s', field, exc)

    def _cover(self, file_id, md5):
        fdata = json.loads(_get(
            'https://libgen.li/json.php?object=f&addkeys=*&ids={}'.format(file_id)
        )).get(file_id, {})
        if fdata.get('cover_exists') == '1' and fdata.get('libgen_id'):
            bucket = (int(fdata['libgen_id']) // 1000) * 1000
            return _get('https://libgen.li/covers/{}/{}_small.jpg'.format(
                bucket, md5 or fdata.get('md5', '')), timeout=10)
        return None

    def _set_id(self, db, book_id, key, val):
        ids      = dict(db.field_for('identifiers', book_id) or {})
        ids[key] = str(val)
        db.set_field('identifiers', {book_id: ids})
