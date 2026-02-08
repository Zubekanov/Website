from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

from util.fcr.file_config_reader import FileConfigReader

logger = logging.getLogger(__name__)

_MC_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
_WHITELIST_LIST_RE = re.compile(r"There are \d+ whitelisted player\(s\):\s*(.*)$", re.IGNORECASE)
_WHITELIST_NONE_RE = re.compile(r"There are no whitelisted players|There are 0 whitelisted player\(s\)", re.IGNORECASE)


@dataclass
class AmpMinecraftConfig:
	base_url: str
	username: str
	password: str
	token: str
	instance_id: str
	instance_name: str
	request_timeout_s: float
	remove_inactive: bool
	startup_reconcile: bool
	whitelist_poll_attempts: int
	whitelist_poll_interval_s: float


def _to_bool(value: str | None, default: bool) -> bool:
	if value is None:
		return default
	v = str(value).strip().lower()
	if v in {"1", "true", "yes", "y", "on"}:
		return True
	if v in {"0", "false", "no", "n", "off"}:
		return False
	return default


def load_amp_minecraft_config() -> AmpMinecraftConfig:
	fcr = FileConfigReader()
	conf = fcr.find("amp_minecraft.conf")
	base_url = (conf.get("AMP_BASE_URL") or "").strip().rstrip("/")
	username = (conf.get("AMP_USERNAME") or "").strip()
	password = (conf.get("AMP_PASSWORD") or "").strip()
	token = (conf.get("AMP_TOKEN") or "").strip()
	instance_id = (conf.get("AMP_INSTANCE_ID") or "").strip()
	instance_name = (conf.get("AMP_INSTANCE_NAME") or "Minecraft").strip()
	timeout_raw = (conf.get("AMP_REQUEST_TIMEOUT_SECONDS") or "20").strip()
	remove_inactive = _to_bool(conf.get("AMP_SYNC_REMOVE_INACTIVE"), True)
	startup_reconcile = _to_bool(conf.get("AMP_STARTUP_RECONCILE"), True)
	poll_attempts_raw = (conf.get("AMP_WHITELIST_POLL_ATTEMPTS") or "10").strip()
	poll_interval_raw = (conf.get("AMP_WHITELIST_POLL_INTERVAL_SECONDS") or "0.3").strip()

	if not base_url or not username or not password:
		raise RuntimeError("amp_minecraft.conf is missing required fields: AMP_BASE_URL, AMP_USERNAME, AMP_PASSWORD.")

	try:
		timeout = float(timeout_raw)
	except Exception:
		timeout = 20.0
	if timeout <= 0:
		timeout = 20.0
	try:
		poll_attempts = int(poll_attempts_raw)
	except Exception:
		poll_attempts = 10
	if poll_attempts <= 0:
		poll_attempts = 10
	try:
		poll_interval = float(poll_interval_raw)
	except Exception:
		poll_interval = 0.3
	if poll_interval <= 0:
		poll_interval = 0.3

	return AmpMinecraftConfig(
		base_url=base_url,
		username=username,
		password=password,
		token=token,
		instance_id=instance_id,
		instance_name=instance_name,
		request_timeout_s=timeout,
		remove_inactive=remove_inactive,
		startup_reconcile=startup_reconcile,
		whitelist_poll_attempts=poll_attempts,
		whitelist_poll_interval_s=poll_interval,
	)


def _normalize_usernames(values: list[str]) -> list[str]:
	out: list[str] = []
	seen: set[str] = set()
	for raw in values:
		val = (raw or "").strip()
		if not _MC_USERNAME_RE.fullmatch(val):
			continue
		key = val.lower()
		if key in seen:
			continue
		seen.add(key)
		out.append(val)
	return out


class AmpMinecraftClient:
	def __init__(self, conf: AmpMinecraftConfig):
		self.conf = conf
		self.session = requests.Session()

	def _post(self, base_url: str, path: str, payload: dict, token: str | None = None):
		headers = {
			"Accept": "application/json",
			"Content-Type": "application/json",
		}
		if token:
			headers["Authorization"] = f"Bearer {token}"
		resp = self.session.post(
			f"{base_url}{path}",
			headers=headers,
			json=payload,
			timeout=self.conf.request_timeout_s,
		)
		resp.raise_for_status()
		if not resp.content:
			return None
		try:
			return resp.json()
		except Exception:
			return {"_raw": resp.text}

	def _login(self, base_url: str) -> str:
		data = self._post(
			base_url,
			"/API/Core/Login",
			{
				"username": self.conf.username,
				"password": self.conf.password,
				"token": self.conf.token,
				"rememberMe": True,
			},
		)
		token = ""
		if isinstance(data, dict):
			token = str(data.get("sessionID") or "")
		if not token:
			raise RuntimeError(f"AMP login failed at {base_url}.")
		return token

	def _find_instance(self, controller_token: str) -> dict:
		if self.conf.instance_id:
			row = self._post(
				self.conf.base_url,
				"/API/ADSModule/GetInstance",
				{"InstanceId": self.conf.instance_id},
				controller_token,
			)
			if not isinstance(row, dict) or not row.get("InstanceID"):
				raise RuntimeError("Configured AMP_INSTANCE_ID was not found.")
			return row

		rows = self._post(self.conf.base_url, "/API/ADSModule/GetLocalInstances", {}, controller_token)
		if not isinstance(rows, list):
			raise RuntimeError("GetLocalInstances returned an unexpected response.")

		want = self.conf.instance_name.lower()
		for row in rows:
			friendly = str(row.get("FriendlyName") or "").lower()
			name = str(row.get("InstanceName") or "").lower()
			module = str(row.get("Module") or "").lower()
			if want and (want == friendly or want == name):
				return row
			if not want and module == "minecraft":
				return row

		if rows:
			for row in rows:
				if str(row.get("Module") or "").lower() == "minecraft":
					return row
		raise RuntimeError("Could not find the Minecraft AMP instance.")

	def _instance_api_base_url(self, instance: dict) -> str:
		port = int(instance.get("Port") or 0)
		if port <= 0:
			raise RuntimeError("Instance Port is missing from AMP metadata.")
		ip = str(instance.get("IP") or "").strip()
		if not ip or ip == "0.0.0.0":
			parsed = urlparse(self.conf.base_url)
			ip = parsed.hostname or "127.0.0.1"
		scheme = "https" if bool(instance.get("IsHTTPS")) else "http"
		return f"{scheme}://{ip}:{port}"

	def _send_console(self, instance_url: str, instance_token: str, command: str) -> None:
		logger.info("AMP Minecraft command send instance=%s command=%s", instance_url, command)
		self._post(
			instance_url,
			"/API/Core/SendConsoleMessage",
			{"message": command},
			instance_token,
		)

	def _get_updates(self, instance_url: str, instance_token: str) -> dict:
		data = self._post(
			instance_url,
			"/API/Core/GetUpdates",
			{},
			instance_token,
		)
		if not isinstance(data, dict):
			return {}
		return data

	def _parse_whitelist_console_entry(self, content: str) -> list[str] | None:
		line = (content or "").strip()
		if not line:
			return None
		match = _WHITELIST_LIST_RE.search(line)
		if match:
			raw = (match.group(1) or "").strip()
			if not raw:
				return []
			return [name.strip() for name in raw.split(",") if name.strip()]
		if _WHITELIST_NONE_RE.search(line):
			return []
		return None

	def _fetch_remote_whitelist(self, instance_url: str, instance_token: str) -> list[str]:
		sent_at = datetime.now(timezone.utc)
		best_fallback: list[str] | None = None
		self._send_console(instance_url, instance_token, "whitelist list")
		for _ in range(self.conf.whitelist_poll_attempts):
			updates = self._get_updates(instance_url, instance_token)
			for entry in reversed(updates.get("ConsoleEntries") or []):
				if not isinstance(entry, dict):
					continue
				content = str(entry.get("Contents") or "")
				parsed = self._parse_whitelist_console_entry(content)
				if parsed is not None:
					if best_fallback is None:
						best_fallback = _normalize_usernames(parsed)
					ts_raw = str(entry.get("Timestamp") or "").strip()
					ts = None
					if ts_raw:
						try:
							ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
						except Exception:
							ts = None
					if ts is None or ts >= (sent_at - timedelta(seconds=2)):
						remote = _normalize_usernames(parsed)
						logger.info("AMP Minecraft remote whitelist fetched count=%s", len(remote))
						return remote
			time.sleep(self.conf.whitelist_poll_interval_s)
		if best_fallback is not None:
			logger.warning("AMP Minecraft using latest known whitelist list output after poll timeout.")
			logger.info("AMP Minecraft remote whitelist fetched count=%s", len(best_fallback))
			return best_fallback
		raise RuntimeError("Timed out waiting for AMP whitelist list output.")

	def reconcile_whitelist(self, *, active_usernames: list[str], inactive_usernames: list[str], dry_run: bool = False) -> dict:
		active = _normalize_usernames(active_usernames)
		inactive = _normalize_usernames(inactive_usernames)

		controller_token = self._login(self.conf.base_url)
		instance = self._find_instance(controller_token)
		instance_url = self._instance_api_base_url(instance)
		instance_token = self._login(instance_url)

		commands: list[str] = []
		errors: list[dict[str, str]] = []
		added = 0
		removed = 0

		try:
			remote = self._fetch_remote_whitelist(instance_url, instance_token)
			remote_map = {name.lower(): name for name in remote}
			active_keys = {name.lower() for name in active}
			to_add = [name for name in active if name.lower() not in remote_map]
			to_remove: list[str] = []
			if self.conf.remove_inactive:
				to_remove = [remote_map[key] for key in remote_map if key not in active_keys]

			for username in to_add:
				cmd = f"whitelist add {username}"
				commands.append(cmd)
				if dry_run:
					logger.info("AMP Minecraft dry-run command instance=%s command=%s", instance_url, cmd)
					continue
				try:
					self._send_console(instance_url, instance_token, cmd)
					added += 1
				except Exception as exc:
					logger.exception("AMP Minecraft command failed instance=%s command=%s", instance_url, cmd)
					errors.append({"username": username, "action": "add", "error": str(exc)})

			for username in to_remove:
				cmd = f"whitelist remove {username}"
				commands.append(cmd)
				if dry_run:
					logger.info("AMP Minecraft dry-run command instance=%s command=%s", instance_url, cmd)
					continue
				try:
					self._send_console(instance_url, instance_token, cmd)
					removed += 1
				except Exception as exc:
					logger.exception("AMP Minecraft command failed instance=%s command=%s", instance_url, cmd)
					errors.append({"username": username, "action": "remove", "error": str(exc)})

			if not dry_run and commands:
				try:
					self._send_console(instance_url, instance_token, "whitelist reload")
					commands.append("whitelist reload")
				except Exception as exc:
					logger.exception("AMP Minecraft command failed instance=%s command=%s", instance_url, "whitelist reload")
					errors.append({"username": "", "action": "reload", "error": str(exc)})
		finally:
			for token, base in ((instance_token, instance_url), (controller_token, self.conf.base_url)):
				try:
					self._post(base, "/API/Core/Logout", {}, token)
				except Exception:
					pass

		return {
			"instance_name": str(instance.get("FriendlyName") or instance.get("InstanceName") or ""),
			"instance_id": str(instance.get("InstanceID") or instance.get("InstanceId") or ""),
			"instance_url": instance_url,
			"dry_run": dry_run,
			"requested_add": len(active),
			"requested_remove": len(inactive) if self.conf.remove_inactive else 0,
			"remote_before_count": len(remote),
			"planned_add": len(to_add),
			"planned_remove": len(to_remove),
			"added": added,
			"removed": removed,
			"commands": commands,
			"errors": errors,
			"ok": len(errors) == 0,
		}

	def sync_whitelist(self, *, active_usernames: list[str], inactive_usernames: list[str], dry_run: bool = False) -> dict:
		active = _normalize_usernames(active_usernames)
		inactive = _normalize_usernames(inactive_usernames)

		controller_token = self._login(self.conf.base_url)
		instance = self._find_instance(controller_token)
		instance_url = self._instance_api_base_url(instance)
		instance_token = self._login(instance_url)

		commands: list[str] = []
		errors: list[dict[str, str]] = []
		added = 0
		removed = 0

		try:
			for username in active:
				cmd = f"whitelist add {username}"
				commands.append(cmd)
				if dry_run:
					logger.info("AMP Minecraft dry-run command instance=%s command=%s", instance_url, cmd)
					continue
				try:
					self._send_console(instance_url, instance_token, cmd)
					added += 1
				except Exception as exc:
					logger.exception("AMP Minecraft command failed instance=%s command=%s", instance_url, cmd)
					errors.append({"username": username, "action": "add", "error": str(exc)})

			if self.conf.remove_inactive:
				for username in inactive:
					cmd = f"whitelist remove {username}"
					commands.append(cmd)
					if dry_run:
						logger.info("AMP Minecraft dry-run command instance=%s command=%s", instance_url, cmd)
						continue
					try:
						self._send_console(instance_url, instance_token, cmd)
						removed += 1
					except Exception as exc:
						logger.exception("AMP Minecraft command failed instance=%s command=%s", instance_url, cmd)
						errors.append({"username": username, "action": "remove", "error": str(exc)})

			if not dry_run:
				try:
					self._send_console(instance_url, instance_token, "whitelist reload")
					commands.append("whitelist reload")
				except Exception as exc:
					logger.exception("AMP Minecraft command failed instance=%s command=%s", instance_url, "whitelist reload")
					errors.append({"username": "", "action": "reload", "error": str(exc)})
		finally:
			for token, base in ((instance_token, instance_url), (controller_token, self.conf.base_url)):
				try:
					self._post(base, "/API/Core/Logout", {}, token)
				except Exception:
					pass

		return {
			"instance_name": str(instance.get("FriendlyName") or instance.get("InstanceName") or ""),
			"instance_id": str(instance.get("InstanceID") or instance.get("InstanceId") or ""),
			"instance_url": instance_url,
			"dry_run": dry_run,
			"requested_add": len(active),
			"requested_remove": len(inactive) if self.conf.remove_inactive else 0,
			"added": added,
			"removed": removed,
			"commands": commands,
			"errors": errors,
			"ok": len(errors) == 0,
		}
