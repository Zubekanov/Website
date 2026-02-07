from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any

from sql.psql_interface import PSQLInterface
from util.fcr.file_config_reader import FileConfigReader


@dataclass
class ApiContext:
	interface: PSQLInterface
	fcr: FileConfigReader
	auth_token_name: str = "session"
	minecraft_status_cache: dict[str, Any] = field(default_factory=lambda: {
		"data": None,
		"fetched_at_ts": None,
		"refreshing": False,
	})
	minecraft_status_lock: threading.Lock = field(default_factory=threading.Lock)
