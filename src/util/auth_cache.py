import time
import threading
from dataclasses import dataclass
from typing import Any

@dataclass
class CacheEntry:
	value: Any
	expires_at: float

class TTLCache:
	def __init__(self, max_items: int = 5000):
		self._max = max_items
		self._lock = threading.RLock()
		self._data: dict[str, CacheEntry] = {}

	def get(self, key: str):
		now = time.time()
		with self._lock:
			ent = self._data.get(key)
			if not ent:
				return None
			if ent.expires_at <= now:
				self._data.pop(key, None)
				return None
			return ent.value

	def set(self, key: str, value, ttl_seconds: int):
		now = time.time()
		with self._lock:
			if len(self._data) >= self._max:
				self._data.pop(next(iter(self._data)))
			self._data[key] = CacheEntry(value=value, expires_at=now + ttl_seconds)

	def delete(self, key: str):
		with self._lock:
			self._data.pop(key, None)

	def clear(self):
		with self._lock:
			self._data.clear()

session_cache = TTLCache(max_items=10000)
