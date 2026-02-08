from __future__ import annotations

import time
from datetime import datetime, timezone
import re
import threading

import flask
import requests

from app.api_context import ApiContext

_MC_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
_AVATAR_CACHE_TTL_SECONDS = 60 * 60
_AVATAR_NEGATIVE_TTL_SECONDS = 5 * 60
_AVATAR_CACHE_MAX_ENTRIES = 256
_avatar_cache_lock = threading.Lock()
_avatar_cache: dict[str, dict[str, object]] = {}


def _avatar_cache_get(username: str) -> tuple[bytes | None, bool, str]:
	now = time.time()
	with _avatar_cache_lock:
		entry = _avatar_cache.get(username.lower())
		if not entry:
			return None, False, "MISS"
		ok = bool(entry.get("ok"))
		ts = float(entry.get("ts") or 0.0)
		content = entry.get("content")
		ttl = _AVATAR_CACHE_TTL_SECONDS if ok else _AVATAR_NEGATIVE_TTL_SECONDS
		age = now - ts
		if age <= ttl:
			if ok and isinstance(content, (bytes, bytearray)) and content:
				return bytes(content), True, "HIT"
			return None, True, "NEGATIVE_HIT"
		return None, False, "EXPIRED"


def _avatar_cache_put(username: str, *, ok: bool, content: bytes | None = None) -> None:
	now = time.time()
	key = username.lower()
	with _avatar_cache_lock:
		_avatar_cache[key] = {
			"ts": now,
			"ok": bool(ok),
			"content": bytes(content) if content else None,
		}
		if len(_avatar_cache) > _AVATAR_CACHE_MAX_ENTRIES:
			oldest_key = min(_avatar_cache.items(), key=lambda item: float(item[1].get("ts") or 0.0))[0]
			_avatar_cache.pop(oldest_key, None)


def _avatar_cache_get_stale_success(username: str) -> bytes | None:
	with _avatar_cache_lock:
		entry = _avatar_cache.get(username.lower())
		if not entry or not bool(entry.get("ok")):
			return None
		content = entry.get("content")
		if not isinstance(content, (bytes, bytearray)) or not content:
			return None
		return bytes(content)


def _avatar_response(content: bytes, cache_state: str) -> flask.Response:
	out = flask.Response(content, status=200, mimetype="image/png")
	out.headers["Cache-Control"] = f"public, max-age={_AVATAR_CACHE_TTL_SECONDS}"
	out.headers["X-Minecraft-Avatar-Cache"] = cache_state
	return out

def _extract_player_names(status: object) -> list[str]:
	try:
		players = getattr(status, "players", None)
		sample = getattr(players, "sample", None) if players is not None else None
		if not sample:
			return []
		out: list[str] = []
		seen: set[str] = set()
		for entry in sample:
			name = str(getattr(entry, "name", "") or "").strip()
			if not name:
				continue
			key = name.lower()
			if key in seen:
				continue
			seen.add(key)
			out.append(name)
		return out
	except Exception:
		return []


def _load_minecraft_target(ctx: ApiContext) -> tuple[str, int]:
	default_host = "mc.zubekanov.com"
	default_port = 25565
	try:
		conf = ctx.fcr.find("minecraft_status.conf")
		if not isinstance(conf, dict):
			return default_host, default_port
		host = (conf.get("MINECRAFT_SERVER_HOST") or "").strip() or default_host
		port_raw = (conf.get("MINECRAFT_SERVER_PORT") or str(default_port)).strip()
		try:
			port = int(port_raw)
		except Exception:
			port = default_port
		if port <= 0 or port > 65535:
			port = default_port
		return host, port
	except Exception:
		return default_host, default_port


def register(api: flask.Blueprint, ctx: ApiContext) -> None:
	@api.route("/api/minecraft/avatar/<username>")
	def api_minecraft_avatar(username: str):
		name = (username or "").strip()
		if not _MC_USERNAME_RE.fullmatch(name):
			return flask.Response(status=404)
		cached, cache_hit, state = _avatar_cache_get(name)
		if cache_hit:
			if cached:
				return _avatar_response(cached, state)
			out = flask.Response(status=404)
			out.headers["X-Minecraft-Avatar-Cache"] = state
			return out
		try:
			resp = requests.get(
				f"https://mc-heads.net/avatar/{name}/24",
				timeout=4,
				headers={"User-Agent": "WebsiteMinecraftAvatarProxy/1.0"},
			)
			if resp.status_code != 200 or not resp.content:
				stale = _avatar_cache_get_stale_success(name)
				if stale:
					return _avatar_response(stale, "STALE")
				_avatar_cache_put(name, ok=False, content=None)
				out = flask.Response(status=404)
				out.headers["X-Minecraft-Avatar-Cache"] = "MISS_NEGATIVE"
				return out
			_avatar_cache_put(name, ok=True, content=resp.content)
			return _avatar_response(resp.content, "MISS")
		except Exception:
			stale = _avatar_cache_get_stale_success(name)
			if stale:
				return _avatar_response(stale, "STALE")
			_avatar_cache_put(name, ok=False, content=None)
			out = flask.Response(status=404)
			out.headers["X-Minecraft-Avatar-Cache"] = "MISS_NEGATIVE"
			return out

	@api.route("/api/minecraft/status")
	def api_minecraft_status():
		host, port = _load_minecraft_target(ctx)

		def fetch_status() -> dict[str, object]:
			start = time.time()
			try:
				from mcstatus import JavaServer
			except Exception as e:
				return {
					"ok": False,
					"error": "Minecraft status service is unavailable.",
					"host": host,
					"port": port,
				}

			try:
				server = JavaServer.lookup(f"{host}:{port}")
				status = server.status()
				latency_ms = int(round((time.time() - start) * 1000))

				# Description can be str or dict depending on mcstatus version
				desc = status.description
				if isinstance(desc, dict):
					motd = desc.get("text") or ""
				else:
					motd = str(desc) if desc is not None else ""

				return {
					"ok": True,
					"online": True,
					"host": host,
					"port": port,
					"motd": motd,
					"players_online": getattr(status.players, "online", None),
					"players_max": getattr(status.players, "max", None),
					"player_names": _extract_player_names(status),
					"version": getattr(status.version, "name", None),
					"latency_ms": latency_ms,
				}
			except Exception as e:
				return {
					"ok": True,
					"online": False,
					"host": host,
					"port": port,
					"error": "Minecraft server is currently unreachable.",
				}
		now = time.time()
		with ctx.minecraft_status_lock:
			cached = ctx.minecraft_status_cache.get("data")
			fetched_at_ts = ctx.minecraft_status_cache.get("fetched_at_ts")
			fetched_at_iso = ctx.minecraft_status_cache.get("fetched_at_iso")

		try:
			data = fetch_status()
			fetched_at = datetime.now(timezone.utc)
			with ctx.minecraft_status_lock:
				ctx.minecraft_status_cache["data"] = data
				ctx.minecraft_status_cache["fetched_at_ts"] = fetched_at.timestamp()
				ctx.minecraft_status_cache["fetched_at_iso"] = fetched_at.isoformat()
				ctx.minecraft_status_cache["refreshing"] = False
			response = dict(data)
			response["cached"] = False
			response["refreshing"] = False
			response["age_seconds"] = 0
			response["fetched_at"] = fetched_at.isoformat()
			return flask.jsonify(response)
		except Exception:
			if cached and fetched_at_ts:
				age = now - float(fetched_at_ts)
				response = dict(cached)
				response["cached"] = True
				response["refreshing"] = False
				response["age_seconds"] = int(age)
				response["fetched_at"] = fetched_at_iso
				return flask.jsonify(response)
			return flask.jsonify({
				"ok": False,
				"error": "Minecraft status unavailable.",
				"host": host,
				"port": port,
				"cached": False,
				"refreshing": False,
				"age_seconds": 0,
				"fetched_at": None,
			})
