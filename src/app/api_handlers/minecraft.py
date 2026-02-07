from __future__ import annotations

import time
from datetime import datetime, timezone

import flask

from app.api_context import ApiContext


def register(api: flask.Blueprint, ctx: ApiContext) -> None:
	@api.route("/api/minecraft/status")
	def api_minecraft_status():
		host = "mc.zubekanov.com"
		port = 25565
		cache_ttl = 300

		def fetch_status() -> dict[str, object]:
			start = time.time()
			try:
				from mcstatus import JavaServer
			except Exception as e:
				return {
					"ok": False,
					"error": f"mcstatus not available: {e}",
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
					"version": getattr(status.version, "name", None),
					"latency_ms": latency_ms,
				}
			except Exception as e:
				return {
					"ok": True,
					"online": False,
					"host": host,
					"port": port,
					"error": str(e),
				}

		def refresh_status_async():
			data = fetch_status()
			fetched_at = datetime.now(timezone.utc)
			with ctx.minecraft_status_lock:
				ctx.minecraft_status_cache["data"] = data
				ctx.minecraft_status_cache["fetched_at_ts"] = fetched_at.timestamp()
				ctx.minecraft_status_cache["fetched_at_iso"] = fetched_at.isoformat()
				ctx.minecraft_status_cache["refreshing"] = False

		now = time.time()
		with ctx.minecraft_status_lock:
			cached = ctx.minecraft_status_cache.get("data")
			fetched_at_ts = ctx.minecraft_status_cache.get("fetched_at_ts")
			refreshing = bool(ctx.minecraft_status_cache.get("refreshing"))

		if cached and fetched_at_ts:
			age = now - float(fetched_at_ts)
			if age < cache_ttl:
				response = dict(cached)
				response["cached"] = True
				response["refreshing"] = False
				response["age_seconds"] = int(age)
				response["fetched_at"] = ctx.minecraft_status_cache.get("fetched_at_iso")
				return flask.jsonify(response)

			if not refreshing:
				with ctx.minecraft_status_lock:
					ctx.minecraft_status_cache["refreshing"] = True
				import threading
				threading.Thread(target=refresh_status_async, daemon=True).start()

			response = dict(cached)
			response["cached"] = True
			response["refreshing"] = True
			response["age_seconds"] = int(age)
			response["fetched_at"] = ctx.minecraft_status_cache.get("fetched_at_iso")
			return flask.jsonify(response)

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
		response["fetched_at"] = ctx.minecraft_status_cache.get("fetched_at_iso")
		return flask.jsonify(response)
