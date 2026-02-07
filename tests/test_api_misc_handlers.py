from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from app.api_handlers import minecraft, ping


def test_ping_route(app_factory):
	ctx = SimpleNamespace(auth_token_name="session", interface=SimpleNamespace(client=SimpleNamespace()), fcr=None)
	app = app_factory(ping.register, ctx)
	client = app.test_client()

	resp = client.get("/api/ping")
	assert resp.status_code == 200
	assert resp.get_json() == {"message": "pong"}


def test_minecraft_status_uses_cache_when_fresh(app_factory):
	now = time.time()
	ctx = SimpleNamespace(
		auth_token_name="session",
		interface=SimpleNamespace(client=SimpleNamespace()),
		fcr=SimpleNamespace(find=lambda _: None),
		minecraft_status_cache={
			"data": {"ok": True, "online": True},
			"fetched_at_ts": now,
			"fetched_at_iso": "2026-01-01T00:00:00+00:00",
			"refreshing": False,
		},
		minecraft_status_lock=threading.Lock(),
	)

	app = app_factory(minecraft.register, ctx)
	client = app.test_client()
	resp = client.get("/api/minecraft/status")

	assert resp.status_code == 200
	body = resp.get_json()
	assert body["cached"] is True
	assert body["refreshing"] is False
	assert body["fetched_at"] == "2026-01-01T00:00:00+00:00"
