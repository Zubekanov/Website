from __future__ import annotations

from types import SimpleNamespace

from app.api_handlers import admin


class _FakeAdminClient:
	def list_tables(self, schema):
		return ["users"]

	def get_table_columns(self, schema, table):
		return ["id", "is_active"]

	def get_primary_key_columns(self, schema, table):
		return ["id"]

	def update_rows_with_equalities(self, table, updates, equalities):
		assert table == "public.users"
		assert updates == {"is_active": True}
		assert equalities == {"id": "u-1"}
		return 1


def test_admin_route_requires_admin(app_factory):
	ctx = SimpleNamespace(
		auth_token_name="session",
		interface=SimpleNamespace(client=_FakeAdminClient()),
		fcr=SimpleNamespace(find=lambda _: None),
	)
	app = app_factory(admin.register, ctx)
	client = app.test_client()

	resp = client.post("/api/admin/db/update-row", json={})
	assert resp.status_code == 401


def test_admin_db_update_row_parses_values(monkeypatch, app_factory):
	ctx = SimpleNamespace(
		auth_token_name="session",
		interface=SimpleNamespace(client=_FakeAdminClient()),
		fcr=SimpleNamespace(find=lambda _: None),
	)
	monkeypatch.setattr(admin, "require_admin", lambda ctx: ({"id": "admin-1", "email": "a@example.com"}, None))

	app = app_factory(admin.register, ctx)
	client = app.test_client()

	resp = client.post(
		"/api/admin/db/update-row",
		json={
			"schema": "public",
			"table": "users",
			"pk__id": "u-1",
			"col__is_active": "true",
		},
	)
	assert resp.status_code == 200
	assert resp.get_json()["ok"] is True


def test_admin_api_access_approve(monkeypatch, app_factory):
	class _Client:
		def update_rows_with_filters(self, table, updates, **kwargs):
			assert table == "api_access_registrations"
			assert updates["status"] == "approved"
			return 1

	class _Interface:
		def __init__(self):
			self.client = _Client()

		def get_api_access_registration_contact_by_id(self, reg_id):
			assert reg_id == "req-1"
			return [{
				"id": reg_id,
				"first_name": "Api",
				"last_name": "User",
				"email": "api@example.com",
				"principal_type": "service",
				"service_name": "worker",
				"requested_scopes": ["metrics.read"],
			}]

	ctx = SimpleNamespace(
		auth_token_name="session",
		interface=_Interface(),
		fcr=SimpleNamespace(find=lambda _: None),
	)
	monkeypatch.setattr(admin, "require_admin", lambda ctx: ({"id": "admin-1", "email": "admin@example.com"}, None))
	monkeypatch.setattr(admin, "send_notification_email", lambda **kwargs: None)
	monkeypatch.setattr(admin, "notify_moderators", lambda *args, **kwargs: None)

	app = app_factory(admin.register, ctx)
	client = app.test_client()
	resp = client.post("/api/admin/api-access/approve", json={"id": "req-1"})
	assert resp.status_code == 200
	assert resp.get_json()["ok"] is True


def test_admin_api_access_deny(monkeypatch, app_factory):
	class _Client:
		def update_rows_with_filters(self, table, updates, **kwargs):
			assert table == "api_access_registrations"
			assert updates["status"] == "denied"
			return 1

	class _Interface:
		def __init__(self):
			self.client = _Client()

		def get_api_access_registration_contact_by_id(self, reg_id):
			return [{
				"id": reg_id,
				"first_name": "Api",
				"last_name": "User",
				"email": "api@example.com",
			}]

	ctx = SimpleNamespace(
		auth_token_name="session",
		interface=_Interface(),
		fcr=SimpleNamespace(find=lambda _: None),
	)
	monkeypatch.setattr(admin, "require_admin", lambda ctx: ({"id": "admin-1", "email": "admin@example.com"}, None))
	monkeypatch.setattr(admin, "send_notification_email", lambda **kwargs: None)
	monkeypatch.setattr(admin, "notify_moderators", lambda *args, **kwargs: None)

	app = app_factory(admin.register, ctx)
	client = app.test_client()
	resp = client.post("/api/admin/api-access/deny", json={"id": "req-1"})
	assert resp.status_code == 200
	assert resp.get_json()["ok"] is True


def test_admin_api_access_pending_count(monkeypatch, app_factory):
	class _Client:
		pass

	class _Interface:
		def __init__(self):
			self.client = _Client()

		def count_pending_api_access_registrations(self):
			return 4

	ctx = SimpleNamespace(
		auth_token_name="session",
		interface=_Interface(),
		fcr=SimpleNamespace(find=lambda _: None),
	)
	monkeypatch.setattr(admin, "require_admin", lambda ctx: ({"id": "admin-1", "email": "admin@example.com"}, None))

	app = app_factory(admin.register, ctx)
	client = app.test_client()
	resp = client.get("/api/admin/api-access/pending-count")
	assert resp.status_code == 200
	assert resp.get_json()["count"] == 4
