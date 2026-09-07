from calibre.customize import StoreBase, FileTypePlugin
from calibre.ebooks.metadata.sources.base import Source


class AnnasArchiveStore(StoreBase):
    name                    = "Anna's Archive"
    description             = 'Search and download books from Libgen.'
    supported_platforms     = ['windows', 'osx', 'linux']
    author                  = 'a-peirogon'
    version                 = (0, 4, 0)
    minimum_calibre_version = (5, 0, 0)
    formats                 = ['EPUB', 'MOBI', 'PDF', 'AZW3', 'CBR', 'CBZ', 'FB2', 'DJVU', 'TXT']
    drm_free_only           = True
    actual_plugin           = 'calibre_plugins.store_annas_archive.annas_archive:AnnasArchiveStore'

    def is_customizable(self):
        return True


class LibgenMetadataSource(Source):
    name                    = 'Libgen'
    description             = 'Downloads metadata and covers from Library Genesis.'
    supported_platforms     = ['windows', 'osx', 'linux']
    author                  = 'a-peirogon'
    version                 = (0, 4, 0)
    minimum_calibre_version = (5, 0, 0)
    actual_plugin           = 'calibre_plugins.store_annas_archive.metadata:LibgenMetadata'
    capabilities            = frozenset({'identify', 'cover'})
    touched_fields          = frozenset({
        'title', 'authors', 'publisher', 'pubdate',
        'comments', 'series', 'language',
        'identifier:isbn', 'identifier:md5', 'identifier:doi',
    })


class LibgenPostImport(FileTypePlugin):
    name                    = 'Libgen Auto Metadata'
    description             = 'Fetches metadata from Libgen for EPUBs added via the store plugin.'
    supported_platforms     = ['windows', 'osx', 'linux']
    author                  = 'a-peirogon'
    version                 = (0, 4, 0)
    minimum_calibre_version = (5, 0, 0)
    file_types              = {'epub'}
    on_import               = False
    on_postimport           = True

    def postimport(self, book_id, book_format, db):
        try:
            from calibre_plugins.store_annas_archive.postimport import LibgenPostImport as _Impl
            _Impl().postimport(book_id, book_format, db)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                'LibgenPostImport failed for book %s: %s', book_id, exc)
