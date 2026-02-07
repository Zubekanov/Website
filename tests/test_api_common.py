from __future__ import annotations

from types import SimpleNamespace

from app import api_common


class _FakeEmitter:
	calls = []

	def __init__(self, interface):
		self.interface = interface

	def emit_event(self, event_key, payload, context):
		self.__class__.calls.append((event_key, payload, context))


class _FakeClient:
	def __init__(self, rows=None, raise_error=False):
		self.rows = rows or []
		self.raise_error = raise_error

	def get_rows_with_filters(self, table, **kwargs):
		if self.raise_error:
			raise RuntimeError("boom")
		return self.rows, len(self.rows)


class _FakeInterface:
	def __init__(self, client=None):
		self.client = client or _FakeClient()

	def _token_secret(self):
		return b"test-secret"


def test_parse_db_value_conversions():
	assert api_common.parse_db_value("  ") is None
	assert api_common.parse_db_value("null") is None
	assert api_common.parse_db_value("true") is True
	assert api_common.parse_db_value("false") is False
	assert api_common.parse_db_value("1") == "1"


def test_build_admin_action_buttons_empty_and_normal():
	assert api_common.build_admin_action_buttons("", "id") == []
	assert api_common.build_admin_action_buttons("minecraft", "reg-1") == [
		{"type": 2, "style": 3, "label": "Approve", "custom_id": "mod:approve:minecraft:reg-1"},
		{"type": 2, "style": 4, "label": "Deny", "custom_id": "mod:deny:minecraft:reg-1"},
	]


def test_build_and_parse_integration_removal_token_roundtrip_and_rejects_tamper():
	ctx = SimpleNamespace(interface=_FakeInterface())
	token = api_common.build_integration_removal_token(
		ctx,
		integration_type="discord_webhook",
		integration_id="int-1",
		user_id="u-1",
	)
	payload = api_common.parse_integration_removal_token(ctx, token)
	assert payload is not None
	assert payload["type"] == "discord_webhook"
	assert payload["id"] == "int-1"
	assert payload["user"] == "u-1"

	tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
	assert api_common.parse_integration_removal_token(ctx, tampered) is None



def test_parse_integration_removal_token_rejects_expired():
	ctx = SimpleNamespace(interface=_FakeInterface())
	token = api_common.build_integration_removal_token(
		ctx,
		integration_type="minecraft",
		integration_id="int-2",
		user_id="u-2",
		ttl_hours=-1,
	)
	assert api_common.parse_integration_removal_token(ctx, token) is None


def test_notify_moderators_builds_payload(monkeypatch):
	monkeypatch.setattr(api_common, "DiscordWebhookEmitter", _FakeEmitter)
	_FakeEmitter.calls = []
	ctx = SimpleNamespace(interface=_FakeInterface())
	buttons = [{"type": 2, "label": f"b{i}"} for i in range(6)]

	api_common.notify_moderators(
		ctx,
		"submitted",
		title="New request",
		actor="user@example.com",
		subject="subject",
		details=["line1", "line2"],
		actions=[("Open", "https://example.com")],
		buttons=buttons,
		context={"k": "v"},
	)

	assert len(_FakeEmitter.calls) == 1
	event_key, payload, context = _FakeEmitter.calls[0]
	assert event_key == "moderator.notifications"
	assert context == {"k": "v"}
	assert payload["components"][0]["components"] == buttons[:5]
	field_names = [f["name"] for f in payload["embeds"][0]["fields"]]
	assert "Action" in field_names
	assert "Details" in field_names
	assert "Actions" in field_names


def test_send_notification_email_happy_path(monkeypatch):
	rendered = {}
	sent = {}

	def _render(template_name, context):
		rendered["name"] = template_name
		rendered["context"] = context
		return "<html>ok</html>"

	def _send_email(**kwargs):
		sent.update(kwargs)

	monkeypatch.setattr(api_common, "render_template", _render)
	monkeypatch.setattr(api_common, "send_email", _send_email)

	api_common.send_notification_email(
		to_email="to@example.com",
		subject="Subject",
		title="Title",
		intro="Intro",
		details=["safe", "<unsafe>"],
		cta_label="Review",
		cta_url="https://example.com?a=1&b=2",
	)

	assert rendered["name"] == "notification.html"
	assert "&lt;unsafe&gt;" in rendered["context"]["details_html"]
	assert "https://example.com?a=1&amp;b=2" in rendered["context"]["cta_html"]
	assert sent["to_addrs"] == ["to@example.com"]
	assert sent["subject"] == "Subject"
	assert "Details:" in sent["body_text"]


def test_send_notification_email_skips_invalid_to(monkeypatch):
	called = {"send": False}
	monkeypatch.setattr(api_common, "send_email", lambda **kwargs: called.__setitem__("send", True))

	api_common.send_notification_email(
		to_email="not-an-email",
		subject="Subject",
		title="Title",
		intro="Intro",
	)

	assert called["send"] is False


def test_get_user_email_and_is_anonymous_user_fallbacks():
	ctx = SimpleNamespace(interface=_FakeInterface(_FakeClient(raise_error=True)))
	assert api_common.get_user_email(ctx, "u-1") is None
	assert api_common.is_anonymous_user(ctx, "u-1") is False


def test_get_user_email_and_is_anonymous_user_success():
	rows = [{"email": " user@example.com ", "is_anonymous": 1}]
	ctx = SimpleNamespace(interface=_FakeInterface(_FakeClient(rows=rows)))
	assert api_common.get_user_email(ctx, "u-1") == "user@example.com"
	assert api_common.is_anonymous_user(ctx, "u-1") is True
