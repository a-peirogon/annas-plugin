import time
import logging
from typing import Dict, Tuple

try:
    load_translations()
except NameError:
    def _(s):
        return s

logger = logging.getLogger(__name__)

__all__ = ('MAX_PAGES_DEFAULT', 'DEFAULT_TIMEOUT', 'TTLCache')

MAX_PAGES_DEFAULT = 20
DEFAULT_TIMEOUT   = 60


class TTLCache:
    def __init__(self, ttl=300):
        self._cache: Dict[str, Tuple] = {}
        self._ttl = ttl

    def get(self, key):
        entry = self._cache.get(key)
        if entry is None:
            return None
        value, ts = entry
        if time.time() - ts > self._ttl:
            del self._cache[key]
            return None
        return value

    def set(self, key, value):
        self._cache[key] = (value, time.time())

    def clear(self):
        self._cache.clear()
