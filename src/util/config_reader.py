import os
import json
from pathlib import Path
from typing import List, Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

class ConfigReader:
    """
    Utility for reading project configuration and managing OAuth credentials.
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
    def _resolve_filename(cls, filename: str) -> Path:
        cfg = cls.config_dir()
        if not Path(filename).suffix:
            candidates = [f for f in cfg.iterdir() if f.stem == filename]
            if len(candidates) == 1:
                return candidates[0]
            if not candidates:
                raise FileNotFoundError(f"No file matching '{filename}' in config dir")
            raise FileNotFoundError(f"Multiple config files match '{filename}'")
        path = cfg / filename
        if not path.exists():
            raise FileNotFoundError(f"Config file '{filename}' not found in config dir")
        return path

    @classmethod
    def get_raw(cls, filename: str) -> str:
        path = cls._resolve_filename(filename)
        return path.read_text(encoding="utf-8")

    @classmethod
    def get_json(cls, filename: str) -> dict:
        return json.loads(cls.get_raw(filename))

    @classmethod
    def get_key_value_config(cls, filename: str) -> dict:
        raw = cls.get_raw(filename)
        cfg = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"): 
                continue
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
        cfg = cls.config_dir()
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
            # save for next time
            with open(creds_path, "w") as token:
                token.write(creds.to_json())
        return creds
