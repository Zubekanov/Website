from __future__ import annotations

import os
from typing import Mapping
from urllib.parse import urlparse

import flask

from util.fcr.file_config_reader import FileConfigReader


def _is_loopback_url(url: str) -> bool:
	try:
		host = (urlparse(url).hostname or "").strip().lower()
	except Exception:
		return False
	return host in {"localhost", "127.0.0.1", "::1"}


def get_public_base_url(
	*,
	fcr: FileConfigReader | None = None,
	env: Mapping[str, str] | None = None,
	default: str = "",
) -> str:
	env_vars = env or os.environ
	configured_url = (env_vars.get("WEBSITE_BASE_URL") or env_vars.get("PUBLIC_BASE_URL") or "").strip()

	if not configured_url and fcr is not None:
		try:
			conf = fcr.find("secrets.conf")
			if isinstance(conf, dict):
				for key in ("WEBSITE_BASE_URL", "PUBLIC_BASE_URL", "BASE_URL"):
					val = (conf.get(key) or "").strip()
					if val:
						configured_url = val
						break
		except Exception:
			pass

	# Prefer the live Flask request host when available, and never emit loopback URLs for real requests.
	if flask.has_request_context():
		try:
			host_url = (flask.request.host_url or "").strip()
			fwd_host = (flask.request.headers.get("X-Forwarded-Host") or "").strip()
			fwd_proto = (flask.request.headers.get("X-Forwarded-Proto") or "").strip() or "https"
			if fwd_host:
				first_host = fwd_host.split(",")[0].strip()
				if first_host:
					forwarded_url = f"{fwd_proto}://{first_host}".rstrip("/")
					if configured_url:
						configured_url = configured_url.rstrip("/")
						if _is_loopback_url(configured_url) and not _is_loopback_url(forwarded_url):
							return forwarded_url
					elif not _is_loopback_url(forwarded_url):
						return forwarded_url
			if host_url:
				host_url = host_url.rstrip("/")
				if configured_url:
					configured_url = configured_url.rstrip("/")
					if _is_loopback_url(configured_url) and not _is_loopback_url(host_url):
						return host_url
					return configured_url
				return host_url
		except Exception:
			pass

	if configured_url:
		return configured_url.rstrip("/")

	if flask.has_app_context():
		try:
			server_name = (flask.current_app.config.get("SERVER_NAME") or "").strip()
			if server_name:
				scheme = (flask.current_app.config.get("PREFERRED_URL_SCHEME") or "https").strip()
				return f"{scheme}://{server_name}".rstrip("/")
		except Exception:
			pass

	return default
