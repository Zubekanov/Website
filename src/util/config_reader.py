import os
import json
from pathlib import Path
from typing import List, Optional, Dict, Any

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

from pathlib import Path

class _DirNS:
	"""
	A directory namespace with a uniform API:
	- get_raw(filename)
	- get_json(filename)
	- get_kv_config(filename)     # key=value lines
	- get_sql(filename)           # split into statements
	- resolve(filename)           # path in this namespace
	"""
	def __init__(self, base: Path):
		self.base = base

	def _resolve(self, filename: str) -> Path:
		# exact path first
		p = self.base / filename
		if p.exists():
			return p
		# fallback: match by stem if no suffix was given
		if not Path(filename).suffix:
			candidates = [f for f in self.base.iterdir() if f.stem == filename]
			if len(candidates) == 1:
				return candidates[0]
			if not candidates:
				raise FileNotFoundError(f"No file matching '{filename}' in {self.base}")
			raise FileNotFoundError(f"Multiple files match stem '{filename}' in {self.base}")
		raise FileNotFoundError(f"File '{filename}' not found in {self.base}")

	def get_raw(self, filename: str) -> str:
		return self._resolve(filename).read_text(encoding="utf-8")

	def get_json(self, filename: str) -> Dict[str, Any]:
		return json.loads(self.get_raw(filename))

	def get_kv_config(self, filename: str) -> Dict[str, str]:
		raw = self.get_raw(filename)
		out: Dict[str, str] = {}
		for line in raw.splitlines():
			s = line.strip()
			if not s or s.startswith("#"):
				continue
			if "=" not in s:
				raise ValueError(f"Invalid config line: '{line}'")
			k, v = s.split("=", 1)
			out[k.strip()] = v.strip()
		return out

	def get_sql(self, filename: str) -> list[str]:
		"""
		Read a SQL file from this namespace and split into statements by ';'.
		Very simple splitter; assumes statements end with ';' and ignores line comments ('--').
		"""
		raw = self.get_raw(filename)
		stmts: list[str] = []
		for chunk in raw.split(";"):
			stmt = chunk.strip()
			if not stmt or stmt.startswith("--"):
				continue
			stmts.append(stmt + ";")
		return stmts

	def resolve(self, filename: str) -> Path:
		return self._resolve(filename)


class ConfigReader:
	"""
	Directory-agnostic config/content reader with named namespaces.
	Usage:
		ConfigReader.sql_dir.get_kv_config("database.config")
		ConfigReader.config_dir.get_json("settings.json")
		ConfigReader.sql_dir.get_sql("init.sql")
	"""
	# ---- base & namespace registry ----
	@staticmethod
	def _base_dir() -> Path:
		return Path(__file__).resolve().parent.parent

	@classmethod
	def _ns_root(cls) -> Path:
		return cls._base_dir()

	# default namespaces
	_NAMESPACES = {
		"config_dir": "config",
		"content_dir": "content",
		"logs_dir": "logs",
		"sql_dir": "sql",
		"lock_dir": "locks",
	}

	@classmethod
	def _ns(cls, name: str) -> _DirNS:
		sub = cls._NAMESPACES.get(name)
		if sub is None:
			raise KeyError(f"Unknown namespace '{name}'")
		return _DirNS(cls._ns_root() / sub)

	# Expose namespaces as properties (so you can call ConfigReader.sql_dir.*)
	@property
	def config_dir(self) -> _DirNS:  return self._ns("config_dir")  # type: ignore[attr-defined]
	@property
	def content_dir(self) -> _DirNS: return self._ns("content_dir") # type: ignore[attr-defined]
	@property
	def logs_dir(self) -> _DirNS:    return self._ns("logs_dir")    # type: ignore[attr-defined]
	@property
	def sql_dir(self) -> _DirNS:     return self._ns("sql_dir")     # type: ignore[attr-defined]
	@property
	def lock_dir(self) -> _DirNS:    return self._ns("lock_dir")    # type: ignore[attr-defined]

	# allow class-level access: ConfigReader.sql_dir.get_...
	# by creating a singleton instance for attribute fallback
	_singleton = None
	def __getattr__(self, item):
		# instance attribute fallback
		if item in self._NAMESPACES:
			return self._ns(item)
		raise AttributeError(item)

	def __class_getitem__(cls, item):
		# not used, but reserved if you want ConfigReader["sql_dir"]
		return cls._ns(item)

	def __new__(cls, *a, **kw):
		# expose namespaces via class attribute access
		if cls._singleton is None:
			cls._singleton = super().__new__(cls)
		return cls._singleton

	# ---- Register custom namespaces at runtime ----
	@classmethod
	def register_namespace(cls, public_name: str, relative_path: str | Path) -> None:
		"""
		Register a new namespace: ConfigReader.<public_name> will map to base/relative_path.
		Example: ConfigReader.register_namespace("assets_dir", "assets")
		         ConfigReader.assets_dir.get_raw("logo.svg")
		"""
		cls._NAMESPACES[public_name] = str(relative_path)

	# ---- Backwards-compatible helpers ----
	@classmethod
	def get_raw(cls, filename: str) -> str:
		return cls().config_dir.get_raw(filename)

	@classmethod
	def get_json(cls, filename: str) -> dict:
		return cls().config_dir.get_json(filename)

	@classmethod
	def get_sql(cls, filename: str) -> list[str]:
		return cls().sql_dir.get_sql(filename)

	@classmethod
	def get_key_value_config(cls, filename: str) -> dict:
		return cls().config_dir.get_kv_config(filename)
	
	@staticmethod
	def get_content_file(filename: str) -> str:
		"""
		Locate a content file under the project's content directory.
		If the extension maps to a preferred subfolder, check there first.
		Otherwise recursively search the content directory.
		"""
		# Base content directory (Path)
		content_dir: Path = ConfigReader().content_dir.base

		# Determine file extension and preferred folder
		ext = Path(filename).suffix.lower()
		folder_map = {
			".js": "scripts",
			".md": "content",
			".html": "content",
			".txt": "content",
		}
		preferred_folder = folder_map.get(ext)

		# Check preferred folder first
		if preferred_folder:
			candidate = content_dir / preferred_folder / filename
			if candidate.exists() and candidate.is_file():
				return str(candidate)

		# Fallback: recursive search
		for path in content_dir.rglob(filename):
			if path.is_file() and path.name == filename:
				return str(path)

		raise FileNotFoundError(f"Content file '{filename}' not found in {content_dir}")

	@staticmethod
	def generate_sql_dict(filename: str) -> None:
		"""
		From a schema.sql file, generates a dict with the table structure.
		"""
		# TODO
		pass

	# ---- OAuth / GCP helpers ----
	@classmethod
	def get_credentials(
		cls,
		client_secrets: str = "credentials.json",
		token_file: str = "token.json",
		scopes: Optional[List[str]] = None,
	) -> Credentials:
		creds_path = cls().config_dir.resolve(token_file)
		secrets_path = cls().config_dir.resolve(client_secrets)
		scopes = scopes or ["https://mail.google.com/"]

		creds = None
		if creds_path.exists():
			creds = Credentials.from_authorized_user_file(str(creds_path), scopes)
		if not creds or not creds.valid:
			if creds and creds.expired and creds.refresh_token:
				creds.refresh(Request())
			else:
				flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), scopes=scopes)
				creds = flow.run_local_server(port=0)
			with open(creds_path, "w") as token:
				token.write(creds.to_json())
		return creds

	@classmethod
	def get_service_account_key_file(cls, filename: str = "service_account.json") -> Path:
		return cls().config_dir.resolve(filename)

	@classmethod
	def set_adc_env(cls, filename: str = "service_account.json") -> None:
		key_path = cls.get_service_account_key_file(filename)
		os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(key_path)
