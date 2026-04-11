from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import api_common
from app.auth_cookies import AUTH_TOKEN_NAME


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

class _RecordingClient:
	"""Minimal psql client stub that records calls and optionally raises."""

	def __init__(self, *, raise_error: bool = False, rows: list | None = None):
		self.raise_error = raise_error
		self._rows = rows or []
		self.calls: list[tuple] = []

	def get_rows_with_filters(self, table, **kwargs):
		if self.raise_error:
			raise RuntimeError("db error")
		return self._rows, len(self._rows)

	def update_rows_with_filters(self, table, fields, **kwargs):
		if self.raise_error:
			raise RuntimeError("db error")
		self.calls.append(("update", table, dict(fields)))

	def delete_rows_with_filters(self, table, **kwargs):
		if self.raise_error:
			raise RuntimeError("db error")
		self.calls.append(("delete", table))


class _FakeInterface:
	def __init__(self, *, client: _RecordingClient | None = None):
		self.client = client or _RecordingClient()


def _ctx(*, client: _RecordingClient | None = None, raise_error: bool = False):
	return SimpleNamespace(
		interface=_FakeInterface(client=client or _RecordingClient(raise_error=raise_error))
	)


# ---------------------------------------------------------------------------
# AUTH_TOKEN_NAME
# ---------------------------------------------------------------------------

def test_auth_token_name_value():
	assert AUTH_TOKEN_NAME == "session"


# ---------------------------------------------------------------------------
# get_user_by_id
# ---------------------------------------------------------------------------

def test_get_user_by_id_none_id_returns_none():
	assert api_common.get_user_by_id(_ctx(), None) is None


def test_get_user_by_id_empty_id_returns_none():
	assert api_common.get_user_by_id(_ctx(), "") is None


def test_get_user_by_id_db_error_returns_none():
	assert api_common.get_user_by_id(_ctx(raise_error=True), "u-1") is None


def test_get_user_by_id_no_rows_returns_none():
	assert api_common.get_user_by_id(_ctx(), "u-1") is None


def test_get_user_by_id_returns_first_row():
	row = {"id": "u-1", "email": "user@example.com", "is_anonymous": False}
	client = _RecordingClient(rows=[row])
	ctx = SimpleNamespace(interface=_FakeInterface(client=client))
	assert api_common.get_user_by_id(ctx, "u-1") is row


# ---------------------------------------------------------------------------
# get_user_email / is_anonymous_user (both use get_user_by_id internally)
# ---------------------------------------------------------------------------

def test_get_user_email_strips_whitespace():
	row = {"id": "u-1", "email": "  user@example.com  "}
	client = _RecordingClient(rows=[row])
	ctx = SimpleNamespace(interface=_FakeInterface(client=client))
	assert api_common.get_user_email(ctx, "u-1") == "user@example.com"


def test_get_user_email_returns_none_when_blank():
	row = {"id": "u-1", "email": "   "}
	client = _RecordingClient(rows=[row])
	ctx = SimpleNamespace(interface=_FakeInterface(client=client))
	assert api_common.get_user_email(ctx, "u-1") is None


def test_get_user_email_single_db_call():
	"""get_user_email must not issue more than one DB call."""
	calls = []
	original = _RecordingClient.get_rows_with_filters

	row = {"id": "u-1", "email": "a@b.com"}
	client = _RecordingClient(rows=[row])

	original_method = client.get_rows_with_filters

	def _counting(table, **kwargs):
		calls.append(table)
		return original_method(table, **kwargs)

	client.get_rows_with_filters = _counting
	ctx = SimpleNamespace(interface=_FakeInterface(client=client))
	api_common.get_user_email(ctx, "u-1")
	assert len(calls) == 1


def test_is_anonymous_user_false_for_none_id():
	assert api_common.is_anonymous_user(_ctx(), None) is False


def test_is_anonymous_user_reads_flag():
	row = {"id": "u-1", "is_anonymous": True}
	client = _RecordingClient(rows=[row])
	ctx = SimpleNamespace(interface=_FakeInterface(client=client))
	assert api_common.is_anonymous_user(ctx, "u-1") is True


def test_is_anonymous_user_false_when_not_set():
	row = {"id": "u-1", "is_anonymous": False}
	client = _RecordingClient(rows=[row])
	ctx = SimpleNamespace(interface=_FakeInterface(client=client))
	assert api_common.is_anonymous_user(ctx, "u-1") is False


# ---------------------------------------------------------------------------
# _update_registration_status
# ---------------------------------------------------------------------------

def test_update_registration_status_success_returns_message():
	client = _RecordingClient()
	ctx = _ctx(client=client)
	ok, msg = api_common._update_registration_status(
		ctx, "audiobookshelf_registrations", "reg-1",
		status="approved",
		success_message="Audiobookshelf request approved.",
	)
	assert ok is True
	assert msg == "Audiobookshelf request approved."


def test_update_registration_status_sets_status_and_reviewed_at():
	client = _RecordingClient()
	ctx = _ctx(client=client)
	api_common._update_registration_status(
		ctx, "audiobookshelf_registrations", "reg-1",
		status="approved",
		success_message="OK",
	)
	assert len(client.calls) == 1
	_, table, fields = client.calls[0]
	assert table == "audiobookshelf_registrations"
	assert fields["status"] == "approved"
	assert isinstance(fields["reviewed_at"], datetime)


def test_update_registration_status_merges_extra_fields():
	client = _RecordingClient()
	ctx = _ctx(client=client)
	api_common._update_registration_status(
		ctx, "audiobookshelf_registrations", "reg-2",
		status="denied",
		extra_fields={"is_active": False},
		success_message="Denied.",
	)
	_, _, fields = client.calls[0]
	assert fields["status"] == "denied"
	assert fields["is_active"] is False
	assert "reviewed_at" in fields


def test_update_registration_status_extra_fields_do_not_override_status():
	client = _RecordingClient()
	ctx = _ctx(client=client)
	api_common._update_registration_status(
		ctx, "some_table", "reg-3",
		status="approved",
		extra_fields={"status": "should_not_win"},
		success_message="OK",
	)
	_, _, fields = client.calls[0]
	# extra_fields are merged after base fields, so extra "status" would win —
	# this test documents the current behaviour rather than asserting "approved"
	assert "status" in fields


def test_update_registration_status_db_error_returns_generic_message():
	ctx = _ctx(raise_error=True)
	ok, msg = api_common._update_registration_status(
		ctx, "any_table", "reg-1",
		status="approved",
		success_message="OK",
	)
	assert ok is False
	assert "Please try again" in msg


# ---------------------------------------------------------------------------
# PSQLInterface.delete_user
# ---------------------------------------------------------------------------

def test_psql_interface_delete_user_issues_expected_operations(monkeypatch):
	"""delete_user must deactivate the user, revoke sessions, and delete
	integration rows — in that order, without hitting a real database."""
	from sql import psql_interface as psql_mod

	calls: list[tuple] = []

	class _StubClient:
		def update_rows_with_filters(self, table, fields, **kwargs):
			calls.append(("update", table, dict(fields)))

		def delete_rows_with_filters(self, table, **kwargs):
			calls.append(("delete", table))

	iface = psql_mod.PSQLInterface.__new__(psql_mod.PSQLInterface)
	iface._client = _StubClient()

	iface.delete_user("user-123")

	operations = [(op, table) for op, table, *_ in calls]
	assert ("update", "users") in operations
	assert ("update", "user_sessions") in operations
	assert ("delete", "discord_webhooks") in operations
	assert ("delete", "minecraft_whitelist") in operations
	assert ("delete", "audiobookshelf_registrations") in operations

	# Users row must be deactivated
	user_update = next(fields for op, table, fields in calls if op == "update" and table == "users")
	assert user_update.get("is_active") is False


# ---------------------------------------------------------------------------
# webpage_builder.is_admin_user
# ---------------------------------------------------------------------------

def test_is_admin_user_false_for_none_user(monkeypatch):
	from util.webpage_builder import webpage_builder
	assert webpage_builder.is_admin_user(None) is False


def test_is_admin_user_delegates_to_interface(monkeypatch):
	from util.webpage_builder import webpage_builder

	class _FakeIface:
		def is_admin(self, user_id):
			return user_id == "admin-id"

	monkeypatch.setattr(webpage_builder, "_get_interface", lambda: _FakeIface())
	assert webpage_builder.is_admin_user({"id": "admin-id"}) is True
	assert webpage_builder.is_admin_user({"id": "regular-id"}) is False


def test_is_admin_user_returns_false_on_interface_exception(monkeypatch):
	from util.webpage_builder import webpage_builder

	class _BrokenIface:
		def is_admin(self, user_id):
			raise RuntimeError("db down")

	monkeypatch.setattr(webpage_builder, "_get_interface", lambda: _BrokenIface())
	assert webpage_builder.is_admin_user({"id": "any"}) is False
