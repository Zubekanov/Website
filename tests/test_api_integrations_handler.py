from __future__ import annotations

import json
from types import SimpleNamespace

from app.api_handlers import integrations


class _FakeClient:
	def __init__(self):
		self.inserted = []
		self.updated = []

	def get_rows_with_filters(self, table, **kwargs):
		if table == "discord_event_keys":
			return ([{"event_key": "moderator.notifications"}], 1)
		return ([], 0)

	def insert_row(self, table, payload):
		row = {"id": "reg-1", **payload}
		self.inserted.append((table, row))
		return row

	def update_rows_with_filters(self, table, updates, **kwargs):
		self.updated.append((table, updates))
		return 1

	def delete_rows_with_filters(self, *args, **kwargs):
		return 1


class _FakeInterface:
	def __init__(self):
		self.client = _FakeClient()

	def is_admin(self, user_id):
		return False

	def get_application_exemption(self, user_id, integration_type):
		return None

	def get_discord_subscription_by_webhook_url_event_key(self, webhook_url, event_key):
		return []

	def get_discord_webhook_by_url(self, webhook_url):
		return [{"id": "wh-1", "is_active": True}]

	def get_discord_webhook_registration_by_url_event_key(self, webhook_url, event_key):
		return []


def test_discord_webhook_verify_anonymous_existing_webhook(monkeypatch, app_factory):
	ctx = SimpleNamespace(
		auth_token_name="session",
		interface=_FakeInterface(),
		fcr=SimpleNamespace(find=lambda _: None),
	)
	monkeypatch.setattr(integrations, "get_request_user", lambda ctx: None)
	monkeypatch.setattr(integrations, "notify_moderators", lambda *args, **kwargs: None)

	app = app_factory(integrations.register, ctx)
	client = app.test_client()
	resp = client.post(
		"/discord-webhook/verify",
		json={
			"name": "Webhook One",
			"webhook_url": "https://discord.com/api/webhooks/1/abc",
			"event_key": "moderator.notifications",
			"first_name": "Anon",
			"last_name": "User",
			"contact_email": "anon@example.com",
		},
	)

	assert resp.status_code == 200
	body = resp.get_json()
	assert body["ok"] is True
	assert "submitted" in body["message"].lower()


def test_discord_webhook_verify_reactivates_existing_subscription(monkeypatch, app_factory):
	class Interface(_FakeInterface):
		def get_discord_subscription_by_webhook_url_event_key(self, webhook_url, event_key):
			return [{"id": "sub-1", "is_active": False, "webhook_active": True}]

	ctx = SimpleNamespace(
		auth_token_name="session",
		interface=Interface(),
		fcr=SimpleNamespace(find=lambda _: None),
	)
	monkeypatch.setattr(integrations, "get_request_user", lambda ctx: None)

	app = app_factory(integrations.register, ctx)
	client = app.test_client()
	resp = client.post(
		"/discord-webhook/verify",
		json={
			"name": "Webhook One",
			"webhook_url": "https://discord.com/api/webhooks/1/abc",
			"event_key": "moderator.notifications",
		},
	)

	assert resp.status_code == 200
	body = resp.get_json()
	assert body["ok"] is True
	assert body["redirect"] == "/discord-webhook/verified?status=reactivated"


def test_api_access_application_submits_pending_for_non_admin(monkeypatch, app_factory):
	ctx = SimpleNamespace(
		auth_token_name="session",
		interface=_FakeInterface(),
		fcr=SimpleNamespace(find=lambda _: None),
	)
	monkeypatch.setattr(integrations, "get_request_user", lambda ctx: {
		"id": "u-1",
		"first_name": "Ada",
		"last_name": "Lovelace",
		"email": "ada@example.com",
	})
	monkeypatch.setattr(integrations, "notify_moderators", lambda *args, **kwargs: None)

	app = app_factory(integrations.register, ctx)
	client = app.test_client()
	resp = client.post(
		"/api-access-application",
		json={
			"principal_type": "service",
			"service_name": "metrics-worker",
			"requested_scopes": "metrics.read, webhook.write",
			"use_case": "Push periodic metrics and webhook events.",
		},
	)

	assert resp.status_code == 200
	body = resp.get_json()
	assert body["ok"] is True
	assert "submitted" in body["message"].lower()
	inserted_table, inserted_row = ctx.interface.client.inserted[-1]
	assert inserted_table == "api_access_registrations"
	assert inserted_row["status"] == "pending"
	assert inserted_row["is_active"] is False
	scopes_value = inserted_row["requested_scopes"]
	if hasattr(scopes_value, "adapted"):
		adapted = scopes_value.adapted
		if isinstance(adapted, str):
			scopes_value = json.loads(adapted)
		else:
			scopes_value = adapted
	assert scopes_value == ["metrics.read", "webhook.write"]


def test_api_access_application_auto_approves_for_admin(monkeypatch, app_factory):
	class _AdminInterface(_FakeInterface):
		def is_admin(self, user_id):
			return True

	ctx = SimpleNamespace(
		auth_token_name="session",
		interface=_AdminInterface(),
		fcr=SimpleNamespace(find=lambda _: None),
	)
	monkeypatch.setattr(integrations, "get_request_user", lambda ctx: {
		"id": "admin-1",
		"first_name": "Admin",
		"last_name": "User",
		"email": "admin@example.com",
	})
	monkeypatch.setattr(integrations, "notify_moderators", lambda *args, **kwargs: None)

	app = app_factory(integrations.register, ctx)
	client = app.test_client()
	resp = client.post(
		"/api-access-application",
		json={
			"principal_type": "service",
			"service_name": "internal-worker",
			"requested_scopes": "admin.api",
			"use_case": "Administrative API tasks.",
		},
	)

	assert resp.status_code == 200
	body = resp.get_json()
	assert body["ok"] is True
	assert "approved" in body["message"].lower()
	inserted_table, inserted_row = ctx.interface.client.inserted[-1]
	assert inserted_table == "api_access_registrations"
	assert inserted_row["status"] == "approved"
	assert inserted_row["is_active"] is True
