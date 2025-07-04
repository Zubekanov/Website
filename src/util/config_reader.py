import os
import json
from pathlib import Path
from typing import List, Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

class ConfigReader:
	"""
	Utility for reading project configuration and managing OAuth and GCP credentials.
	"""
	@staticmethod
	def _base_dir() -> Path:
		return Path(__file__).resolve().parent.parent

	@classmethod
	def config_dir(cls) -> Path:
		return cls._base_dir() / "config"

	@classmethod
	def content_dir(cls) -> Path:
		return cls._base_dir() / "content"

	@classmethod
	def logs_dir(cls) -> Path:
		return cls._base_dir() / "logs"
	
	@classmethod
	def sql_dir(cls) -> Path:
		return cls._base_dir() / "sql"

	@classmethod
	def lock_dir(cls) -> Path:
		return cls._base_dir() / "locks"
	
	@staticmethod
	def get_content_file(filename: str) -> str:
		"""
		Locate a content file under the project's content directory.
		If the extension maps to a preferred subfolder, check there first.
		Otherwise recursively search the content directory.
		"""
		# Base content directory (Path)
		content_dir = ConfigReader.content_dir()

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
			if path.is_file():
				return str(path)

		raise FileNotFoundError(f"Content file '{filename}' not found in {content_dir}")

	@classmethod
	def _resolve_filename(cls, filename: str) -> Path:
		cfg = cls.config_dir()
		path = cfg / filename
		if path.exists():
			return path
		# fallback: match by stem if no suffix
		if not Path(filename).suffix:
			candidates = [f for f in cfg.iterdir() if f.stem == filename]
			if len(candidates) == 1:
				return candidates[0]
			if not candidates:
				raise FileNotFoundError(f"No file matching '{filename}' in config dir")
			raise FileNotFoundError(f"Multiple config files match '{filename}'")
		raise FileNotFoundError(f"Config file '{filename}' not found in config dir")

	@classmethod
	def get_raw(cls, filename: str) -> str:
		path = cls._resolve_filename(filename)
		return path.read_text(encoding="utf-8")

	@classmethod
	def get_json(cls, filename: str) -> dict:
		return json.loads(cls.get_raw(filename))
	
	@classmethod
	def get_sql(cls, filename: str) -> list:
		"""
		Read a SQL file from the sql directory.
		Returns as a list of sql statements split by semicolon.
		"""
		sql_path = cls.sql_dir() / filename
		if not sql_path.exists():
			raise FileNotFoundError(f"SQL file '{filename}' not found in sql dir")
		raw_sql = sql_path.read_text(encoding="utf-8")
		# Split by semicolon, ignoring comments and empty lines
		statements = []
		for statement in raw_sql.split(";"):
			stmt = statement.strip() + ";"
			if stmt and not stmt.startswith("--") and not stmt == ";":
				statements.append(stmt)
		return statements

	@classmethod
	def get_key_value_config(cls, filename: str) -> dict:
		raw = cls.get_raw(filename)
		cfg = {}
		for line in raw.splitlines():
			line = line.strip()
			if not line or line.startswith("#"):  continue
			if "=" not in line:
				raise ValueError(f"Invalid config line: '{line}'")
			key, val = line.split("=", 1)
			cfg[key.strip()] = val.strip()
		return cfg

	@classmethod
	def get_credentials(
		cls,
		client_secrets: str = "credentials.json",
		token_file: str = "token.json",
		scopes: Optional[List[str]] = None,
	) -> Credentials:
		"""
		Load OAuth2 credentials from token_file in config dir, refreshing if expired.
		If token_file does not exist, perform interactive auth using client_secrets.
		"""
		creds_path = cls._resolve_filename(token_file)
		secrets_path = cls._resolve_filename(client_secrets)
		scopes = scopes or ["https://mail.google.com/"]

		creds = None
		if creds_path.exists():
			creds = Credentials.from_authorized_user_file(str(creds_path), scopes)
		if not creds or not creds.valid:
			if creds and creds.expired and creds.refresh_token:
				creds.refresh(Request())
			else:
				flow = InstalledAppFlow.from_client_secrets_file(
					str(secrets_path), scopes=scopes
				)
				creds = flow.run_local_server(port=0)
			with open(creds_path, "w") as token:
				token.write(creds.to_json())
		return creds

	@classmethod
	def get_service_account_key_file(cls, filename: str = "service_account.json") -> Path:
		"""
		Return the path to a GCP service account JSON key in the config dir.
		"""
		return cls._resolve_filename(filename)

	@classmethod
	def set_adc_env(cls, filename: str = "service_account.json") -> None:
		"""
		Set GOOGLE_APPLICATION_CREDENTIALS env var to a service account key in config dir.
		"""
		key_path = cls.get_service_account_key_file(filename)
		os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(key_path)
