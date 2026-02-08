from __future__ import annotations

import time
from datetime import datetime, timezone

import flask

from app.api_context import ApiContext


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
