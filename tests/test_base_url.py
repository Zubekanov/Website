from __future__ import annotations

from types import SimpleNamespace

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
