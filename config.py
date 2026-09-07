import time

from calibre_plugins.store_annas_archive.constants import DEFAULT_TIMEOUT, MAX_PAGES_DEFAULT

try:
    from qt.core import (
        Qt, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
        QGroupBox, QSpinBox, QPushButton, QThread, pyqtSignal,
        QListWidget, QListWidgetItem, QAbstractItemView,
    )
except (ImportError, ModuleNotFoundError):
    from PyQt5.QtCore import Qt, QThread
    from PyQt5.QtCore import pyqtSignal
    from PyQt5.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel,
        QGroupBox, QSpinBox, QPushButton,
        QListWidget, QListWidgetItem, QAbstractItemView,
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
        import socket
        from urllib.parse import urlparse
        import http.client as hc
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

        adv = QGroupBox(_('Advanced'))
        gl  = QVBoxLayout(adv)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel(_('Max pages:')))
        self._max_pages = QSpinBox()
        self._max_pages.setRange(1, 100)
        self._max_pages.setValue(MAX_PAGES_DEFAULT)
        row1.addWidget(self._max_pages)
        row1.addStretch()
        gl.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel(_('Timeout (s):')))
        self._timeout = QSpinBox()
        self._timeout.setRange(5, 300)
        self._timeout.setValue(DEFAULT_TIMEOUT)
        row2.addWidget(self._timeout)
        row2.addStretch()
        gl.addLayout(row2)

        root.addWidget(adv)

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

    def save_settings(self):
        cfg = self._store.config or {}
        cfg['max_pages'] = self._max_pages.value()
        cfg['timeout']   = self._timeout.value()
        self._store.config = cfg

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
