import json
import os
import time
import logging
from typing import Any, Dict, Optional, Tuple

try:
    load_translations()
except NameError:
    def _(s):
        return s

logger = logging.getLogger(__name__)

__all__ = ('MAX_PAGES_DEFAULT', 'DEFAULT_TIMEOUT', 'LANGUAGES', 'TTLCache', 'DiskCache')

MAX_PAGES_DEFAULT = 20
DEFAULT_TIMEOUT   = 60

LANGUAGES = [
    ('', _('All languages')),
    ('English',    'English'),
    ('Spanish',    'Spanish'),
    ('French',     'French'),
    ('German',     'German'),
    ('Portuguese', 'Portuguese'),
    ('Italian',    'Italian'),
    ('Russian',    'Russian'),
    ('Chinese',    'Chinese'),
    ('Japanese',   'Japanese'),
    ('Arabic',     'Arabic'),
]


class TTLCache:
    def __init__(self, ttl: int = 300):
        self._cache: Dict[str, Tuple] = {}
        self._ttl = ttl

    def get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        value, ts = entry
        if time.time() - ts > self._ttl:
            del self._cache[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._cache[key] = (value, time.time())

    def clear(self) -> None:
        self._cache.clear()


class DiskCache:
    def __init__(self, path: str, ttl: int = 86400):
        self._path = path
        self._ttl  = ttl
        self._mem: Dict[str, Tuple] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            now = time.time()
            self._mem = {
                k: (v, ts) for k, (v, ts) in raw.items()
                if now - ts < self._ttl
            }
        except Exception:
            self._mem = {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, 'w', encoding='utf-8') as f:
                json.dump(self._mem, f, ensure_ascii=False, separators=(',', ':'))
        except Exception as exc:
            logger.debug('DiskCache save failed: %s', exc)

    def get(self, key: str) -> Optional[Any]:
        entry = self._mem.get(key)
        if entry is None:
            return None
        value, ts = entry
        if time.time() - ts > self._ttl:
            del self._mem[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._mem[key] = (value, time.time())
        self._save()

    def clear(self) -> None:
        self._mem.clear()
        self._save()
