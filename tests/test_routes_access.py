from __future__ import annotations

import flask
import importlib
import pytest
import sys
from types import ModuleType


def _build_stub_webpage_builder_module() -> ModuleType:
	mod = ModuleType("util.webpage_builder.webpage_builder")

	def _builder(*args, **kwargs):
		return "OK:builder"

	builder_names = [
		"build_admin_api_access_approvals_page",
		"build_admin_audiobookshelf_approvals_page",
		"build_admin_dashboard_page",
		"build_admin_discord_webhook_approvals_page",
		"build_admin_email_debug_page",
		"build_admin_frontend_test_page",
		"build_admin_minecraft_approvals_page",
		"build_admin_users_page",
		"build_api_access_application_page",
		"build_audiobookshelf_registration_page",
		"build_audiobookshelf_unavailable_page",
		"build_delete_account_page",
		"build_discord_webhook_registration_page",
		"build_discord_webhook_verify_page",
		"build_discord_webhook_verified_page",
		"build_empty_landing_page",
		"build_error_page",
		"build_integration_remove_page",
		"build_integration_removed_page",
		"build_login_page",
		"build_minecraft_page",
		"build_popugame_invalid_link_page",
		"build_popugame_page",
		"build_profile_page",
		"build_psql_interface_page",
		"build_readme_page",
		"build_register_page",
		"build_reset_password_page",
		"build_server_metrics_page",
		"build_verify_email_page",
		"build_verify_email_token_page",
		"build_501_page",
	]
	for name in builder_names:
		setattr(mod, name, _builder)

	setattr(mod, "_is_admin_user", lambda user: bool(user and user.get("id") == "admin-1"))
	return mod

def _import_routes_with_stubs() -> ModuleType:
	stub_um = ModuleType("util.user_management")

	class _UserManagement:
		@staticmethod
		def get_user_by_session_token(token: str):
			if token == "member-token":
				return {"id": "user-1", "first_name": "Member", "last_name": "User", "email": "member@example.com"}
			if token == "admin-token":
				return {"id": "admin-1", "first_name": "Admin", "last_name": "User", "email": "admin@example.com"}
			return None

	stub_um.UserManagement = _UserManagement
	sys.modules["util.user_management"] = stub_um
	sys.modules["util.webpage_builder.webpage_builder"] = _build_stub_webpage_builder_module()
	sys.modules.pop("app.routes", None)
	return importlib.import_module("app.routes")


def _build_main_app(routes_mod: ModuleType) -> flask.Flask:
	app = flask.Flask(__name__)
	app.config["TESTING"] = True
	app.register_blueprint(routes_mod.main)
	return app


def _collect_protected_routes(app: flask.Flask, routes_mod: ModuleType) -> list[tuple[str, str, dict[str, str]]]:
	protected: list[tuple[str, str, dict[str, str]]] = []
	for fn_name, cfg in routes_mod.PAGE_ACCESS_REQUIREMENTS.items():
		endpoint = f"main.{fn_name}"
		rules = [r for r in app.url_map.iter_rules() if r.endpoint == endpoint and not r.arguments and "GET" in r.methods]
		assert rules, f"No GET rule found for protected endpoint {endpoint}"
		protected.append((fn_name, rules[0].rule, cfg))
	return protected


@pytest.mark.parametrize("actor", ["anonymous", "member", "admin"])
def test_decorated_routes_enforce_access(actor: str):
	routes_mod = _import_routes_with_stubs()
	app = _build_main_app(routes_mod)
	client = app.test_client()

	if actor == "member":
		client.set_cookie(routes_mod._AUTH_TOKEN_NAME_, "member-token")
	elif actor == "admin":
		client.set_cookie(routes_mod._AUTH_TOKEN_NAME_, "admin-token")

	for _fn_name, path, cfg in _collect_protected_routes(app, routes_mod):
		resp = client.get(path, follow_redirects=False)
		level = cfg["level"]
		if level == "auth":
			if actor == "anonymous":
				assert resp.status_code == 302
				assert resp.headers.get("Location", "").endswith(cfg["unauth_redirect"])
			else:
				assert resp.status_code == 200
		elif level == "anon":
			if actor == "anonymous":
				assert resp.status_code == 200
			else:
				assert resp.status_code == 302
				assert resp.headers.get("Location", "").endswith(cfg["auth_redirect"])
		elif level == "admin":
			if actor == "anonymous":
				assert resp.status_code == 302
				assert resp.headers.get("Location", "").endswith("/login")
			elif actor == "member":
				assert resp.status_code == 403
			else:
				assert resp.status_code == 200
		else:
			pytest.fail(f"Unhandled access level: {level}")
