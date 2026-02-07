from __future__ import annotations

from app.api_handlers import discord


def test_discord_interactions_rejects_invalid_signature(app_factory, simple_ctx):
	app = app_factory(discord.register, simple_ctx)
	client = app.test_client()

	resp = client.post("/api/discord/interactions", json={"type": 1})
	assert resp.status_code == 401


def test_discord_interactions_ping(monkeypatch, app_factory, simple_ctx):
	monkeypatch.setattr(discord, "verify_discord_signature", lambda signature, timestamp, body, ctx: True)
	app = app_factory(discord.register, simple_ctx)
	client = app.test_client()

	resp = client.post("/api/discord/interactions", json={"type": 1})
	assert resp.status_code == 200
	assert resp.get_json() == {"type": 1}


def test_discord_interactions_component_dispatch(monkeypatch, app_factory, simple_ctx):
	monkeypatch.setattr(discord, "verify_discord_signature", lambda signature, timestamp, body, ctx: True)
	monkeypatch.setattr(discord, "handle_mod_action", lambda ctx, kind, action, reg_id: (True, f"{kind}:{action}:{reg_id}"))
	app = app_factory(discord.register, simple_ctx)
	client = app.test_client()

	resp = client.post(
		"/api/discord/interactions",
		json={"type": 3, "data": {"custom_id": "mod:approve:minecraft:abc"}},
	)
	assert resp.status_code == 200
	assert "minecraft:approve:abc" in resp.get_json()["data"]["content"]


def test_discord_interactions_component_invalid_custom_id(monkeypatch, app_factory, simple_ctx):
	monkeypatch.setattr(discord, "verify_discord_signature", lambda signature, timestamp, body, ctx: True)
	app = app_factory(discord.register, simple_ctx)
	client = app.test_client()

	resp = client.post("/api/discord/interactions", json={"type": 3, "data": {"custom_id": "bad"}})
	assert resp.status_code == 200
	assert resp.get_json()["data"]["content"] == "Unsupported action."


def test_discord_interactions_unsupported_type(monkeypatch, app_factory, simple_ctx):
	monkeypatch.setattr(discord, "verify_discord_signature", lambda signature, timestamp, body, ctx: True)
	app = app_factory(discord.register, simple_ctx)
	client = app.test_client()

	resp = client.post("/api/discord/interactions", json={"type": 9})
	assert resp.status_code == 200
	assert resp.get_json()["data"]["content"] == "Unsupported interaction type."
