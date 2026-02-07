from __future__ import annotations

from types import SimpleNamespace

import flask

from util.base_url import get_public_base_url


def test_get_public_base_url_prefers_env():
	fcr = SimpleNamespace(find=lambda _: {"WEBSITE_BASE_URL": "https://from-config.example"})
	env = {"WEBSITE_BASE_URL": "https://from-env.example"}
	assert get_public_base_url(fcr=fcr, env=env) == "https://from-env.example"


def test_get_public_base_url_uses_config_order():
	fcr = SimpleNamespace(find=lambda _: {"PUBLIC_BASE_URL": "https://public.example", "BASE_URL": "https://base.example"})
	assert get_public_base_url(fcr=fcr, env={}) == "https://public.example"


def test_get_public_base_url_falls_back_to_default():
	fcr = SimpleNamespace(find=lambda _: {})
	assert get_public_base_url(fcr=fcr, env={}, default="http://fallback.local") == "http://fallback.local"


def test_get_public_base_url_uses_flask_request_host():
	app = flask.Flask(__name__)
	with app.test_request_context("/", base_url="https://example.test:8443"):
		assert get_public_base_url(fcr=None, env={}) == "https://example.test:8443"


def test_get_public_base_url_ignores_loopback_config_when_request_is_public():
	app = flask.Flask(__name__)
	with app.test_request_context("/", base_url="https://prod.example.com"):
		env = {"WEBSITE_BASE_URL": "http://localhost:5000"}
		assert get_public_base_url(fcr=None, env=env) == "https://prod.example.com"


def test_get_public_base_url_prefers_forwarded_host_when_config_is_loopback():
	app = flask.Flask(__name__)
	with app.test_request_context(
		"/",
		base_url="http://127.0.0.1:5000",
		headers={"X-Forwarded-Host": "app.example.com", "X-Forwarded-Proto": "https"},
	):
		env = {"WEBSITE_BASE_URL": "http://localhost:5000"}
		assert get_public_base_url(fcr=None, env=env) == "https://app.example.com"
