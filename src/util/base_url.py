from __future__ import annotations

import os
from typing import Mapping

from util.fcr.file_config_reader import FileConfigReader


def get_public_base_url(
	*,
	fcr: FileConfigReader | None = None,
	env: Mapping[str, str] | None = None,
	default: str = "http://localhost:5000",
) -> str:
	env_vars = env or os.environ
	env_url = (env_vars.get("WEBSITE_BASE_URL") or env_vars.get("PUBLIC_BASE_URL") or "").strip()
	if env_url:
		return env_url

	if fcr is not None:
		try:
			conf = fcr.find("secrets.conf")
			if isinstance(conf, dict):
				for key in ("WEBSITE_BASE_URL", "PUBLIC_BASE_URL", "BASE_URL"):
					val = (conf.get(key) or "").strip()
					if val:
						return val
		except Exception:
			pass

	return default
