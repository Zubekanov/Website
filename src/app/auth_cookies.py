from __future__ import annotations

import os

import flask

AUTH_TOKEN_NAME = "session"


def _coerce_bool(value: object, *, default: bool) -> bool:
	if value is None:
		return default
	if isinstance(value, bool):
		return value

	normalized = str(value).strip().lower()
	if normalized in {"1", "true", "yes", "on"}:
		return True
	if normalized in {"0", "false", "no", "off"}:
		return False
	return default


def auth_cookie_secure(*, default: bool = True) -> bool:
	if flask.has_app_context():
		configured = flask.current_app.config.get("AUTH_COOKIE_SECURE")
		if configured is not None:
			return _coerce_bool(configured, default=default)

	return _coerce_bool(os.environ.get("AUTH_COOKIE_SECURE"), default=default)


def session_cookie_kwargs(*, value: str, max_age: int) -> dict[str, object]:
	return {
		"value": value,
		"httponly": True,
		"secure": auth_cookie_secure(),
		"samesite": "Lax",
		"max_age": max_age,
		"path": "/",
	}
