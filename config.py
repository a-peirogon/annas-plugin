import os
import time

from calibre_plugins.store_annas_archive.constants import (
    DEFAULT_TIMEOUT, MAX_PAGES_DEFAULT, LANGUAGES
)

try:
    from qt.core import (
        Qt, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
        QSpinBox, QPushButton, QThread, pyqtSignal, QComboBox,
        QListWidget, QListWidgetItem, QAbstractItemView, QCheckBox,
    )
except (ImportError, ModuleNotFoundError):
    from PyQt5.QtCore import Qt, QThread, pyqtSignal
    from PyQt5.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
        QSpinBox, QPushButton, QComboBox,
        QListWidget, QListWidgetItem, QAbstractItemView, QCheckBox,
    )

from calibre_plugins.store_annas_archive.annas_archive import LIBGEN_MIRRORS

load_translations()


class MirrorTestWorker(QThread):
    result = pyqtSignal(str, bool, float)

    def __init__(self, mirrors):
        super().__init__()
        self._mirrors = mirrors
        self._stop    = False

    def run(self):
        import http.client as hc
        from urllib.parse import urlparse
        from calibre_plugins.store_annas_archive.annas_archive import USER_AGENT
        for mirror in self._mirrors:
            if self._stop:
                break
            t0 = time.time()
            ok = False
            try:
                p   = urlparse(mirror)
                cls = hc.HTTPSConnection if p.scheme == 'https' else hc.HTTPConnection
                c   = cls(p.netloc, timeout=8)
                c.request('HEAD', '/', headers={'User-Agent': USER_AGENT})
                ok  = c.getresponse().status < 500
                c.close()
            except Exception:
                pass
            self.result.emit(mirror, ok, time.time() - t0)

    def stop(self):
        self._stop = True


class ConfigWidget(QWidget):
    def __init__(self, store):
        super().__init__()
        self._store  = store
        self._worker = None
        self._items  = {}
        self._build()
        self._load()

    def _build(self):
        root = QVBoxLayout(self)

        search = QGroupBox(_('Search'))
        sl     = QVBoxLayout(search)

        row_lang = QHBoxLayout()
        row_lang.addWidget(QLabel(_('Language:')))
        self._lang = QComboBox()
        for code, label in LANGUAGES:
            self._lang.addItem(label, code)
        row_lang.addWidget(self._lang)
        row_lang.addStretch()
        sl.addLayout(row_lang)

        row_pages = QHBoxLayout()
        row_pages.addWidget(QLabel(_('Max pages:')))
        self._max_pages = QSpinBox()
        self._max_pages.setRange(1, 100)
        self._max_pages.setValue(MAX_PAGES_DEFAULT)
        row_pages.addWidget(self._max_pages)
        row_pages.addStretch()
        sl.addLayout(row_pages)

        row_to = QHBoxLayout()
        row_to.addWidget(QLabel(_('Timeout (s):')))
        self._timeout = QSpinBox()
        self._timeout.setRange(5, 300)
        self._timeout.setValue(DEFAULT_TIMEOUT)
        row_to.addWidget(self._timeout)
        row_to.addStretch()
        sl.addLayout(row_to)

        root.addWidget(search)

        cache = QGroupBox(_('Cache'))
        cl    = QVBoxLayout(cache)

        row_cache = QHBoxLayout()
        self._cache_enabled = QCheckBox(_('Persist search cache to disk'))
        row_cache.addWidget(self._cache_enabled)
        cl.addLayout(row_cache)

        row_ttl = QHBoxLayout()
        row_ttl.addWidget(QLabel(_('Cache TTL (hours):')))
        self._cache_ttl = QSpinBox()
        self._cache_ttl.setRange(1, 168)
        self._cache_ttl.setValue(24)
        row_ttl.addWidget(self._cache_ttl)

        self._clear_cache = QPushButton(_('Clear cache'))
        self._clear_cache.clicked.connect(self._on_clear_cache)
        row_ttl.addWidget(self._clear_cache)
        row_ttl.addStretch()
        cl.addLayout(row_ttl)

        root.addWidget(cache)

        mir = QGroupBox(_('Mirrors'))
        ml  = QVBoxLayout(mir)

        self._mirror_list = QListWidget()
        self._mirror_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        for m in LIBGEN_MIRRORS:
            item = QListWidgetItem(m)
            self._mirror_list.addItem(item)
            self._items[m] = item
        ml.addWidget(self._mirror_list)

        self._test_btn = QPushButton(_('Test mirrors'))
        self._test_btn.clicked.connect(self._test_mirrors)
        ml.addWidget(self._test_btn)

        root.addWidget(mir)

    def _load(self):
        cfg = self._store.config or {}
        self._max_pages.setValue(cfg.get('max_pages', MAX_PAGES_DEFAULT))
        self._timeout.setValue(cfg.get('timeout', DEFAULT_TIMEOUT))
        self._cache_enabled.setChecked(cfg.get('cache_disk', False))
        self._cache_ttl.setValue(cfg.get('cache_ttl_hours', 24))
        lang = cfg.get('language', '')
        for i in range(self._lang.count()):
            if self._lang.itemData(i) == lang:
                self._lang.setCurrentIndex(i)
                break

    def save_settings(self):
        cfg = self._store.config or {}
        cfg['max_pages']       = self._max_pages.value()
        cfg['timeout']         = self._timeout.value()
        cfg['language']        = self._lang.currentData()
        cfg['cache_disk']      = self._cache_enabled.isChecked()
        cfg['cache_ttl_hours'] = self._cache_ttl.value()
        self._store.config     = cfg

    def _on_clear_cache(self):
        try:
            from calibre_plugins.store_annas_archive.annas_archive import AnnasArchiveStore
            self._store._cache.clear()
        except Exception:
            pass
        try:
            import os
            path = self._cache_path()
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        self._clear_cache.setText(_('Cleared!'))

    def _cache_path(self):
        from calibre.utils.config import config_dir
        return os.path.join(config_dir, 'cal_libgen_cache.json')

    def _test_mirrors(self):
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait()
        mirrors = LIBGEN_MIRRORS
        for m in mirrors:
            if m in self._items:
                self._items[m].setText('{} …'.format(m))
        self._worker = MirrorTestWorker(mirrors)
        self._worker.result.connect(self._on_result)
        self._worker.start()

    def _on_result(self, mirror, ok, latency):
        if mirror in self._items:
            status = '✓ {:.0f}ms'.format(latency * 1000) if ok else '✗'
            self._items[mirror].setText('{} {}'.format(mirror, status))
