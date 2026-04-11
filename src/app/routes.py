from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
import logging
import time
import traceback

import flask
from flask import g
import requests
from werkzeug.exceptions import HTTPException

from app.auth_cookies import AUTH_TOKEN_NAME, session_cookie_kwargs
from util.user_management import UserManagement
from util.webpage_builder import webpage_builder as page_builders

logger = logging.getLogger(__name__)
main = flask.Blueprint("main", __name__)
PAGE_ACCESS_REQUIREMENTS: dict[str, dict[str, str]] = {}


@dataclass(frozen=True)
class PageRoute:
	path: str
	endpoint: str
	builder_name: str
	access: str | None = None
	methods: tuple[str, ...] = ("GET",)
	unauth_redirect: str = "/login"
	auth_redirect: str = "/profile"


def page_access(level: str, *, unauth_redirect: str = "/login", auth_redirect: str = "/profile"):
	allowed = {"public", "auth", "anon", "admin"}
	if level not in allowed:
		raise ValueError(f"Invalid page access level: {level}")

	def _decorator(fn):
		PAGE_ACCESS_REQUIREMENTS[fn.__name__] = {
			"level": level,
			"unauth_redirect": unauth_redirect,
			"auth_redirect": auth_redirect,
		}

		@wraps(fn)
		def _wrapped(*args, **kwargs):
			user = getattr(g, "user", None)
			if level == "auth" and not user:
				return flask.redirect(unauth_redirect)
			if level == "anon" and user:
				return flask.redirect(auth_redirect)
			if level == "admin":
				if not user:
					return flask.redirect("/login")
				if not page_builders.is_admin_user(user):
					err = HTTPException()
					err.code = 403
					err.description = "Admin access required."
					return page_builders.build_error_page(user, err), 403
			return fn(*args, **kwargs)

		return _wrapped

	return _decorator


def _register_page_route(spec: PageRoute) -> None:
	def _view(**kwargs):
		builder = getattr(page_builders, spec.builder_name)
		return builder(getattr(g, "user", None), **kwargs)

	_view.__name__ = spec.endpoint
	view = _view
	if spec.access in {"auth", "anon", "admin"}:
		view = page_access(
			spec.access,
			unauth_redirect=spec.unauth_redirect,
			auth_redirect=spec.auth_redirect,
		)(view)
	main.add_url_rule(
		spec.path,
		endpoint=spec.endpoint,
		view_func=view,
		methods=list(spec.methods),
	)
	globals()[spec.endpoint] = view


_PAGE_ROUTES = (
	PageRoute("/readme", "readme_page", "build_readme_page"),
	PageRoute("/server-metrics", "server_metrics_page", "build_server_metrics_page"),
	PageRoute("/profile", "profile_page", "build_profile_page", access="auth", unauth_redirect="/login"),
	PageRoute("/login", "login_page", "build_login_page", access="anon", auth_redirect="/profile"),
	PageRoute("/register", "register_page", "build_register_page", access="anon", auth_redirect="/profile"),
	PageRoute("/reset-password", "reset_password_page", "build_reset_password_page"),
	PageRoute("/delete-account", "delete_account_page", "build_delete_account_page", access="auth", unauth_redirect="/"),
	PageRoute("/verify-email", "verify_email_page", "build_verify_email_page"),
	PageRoute("/verify-email/<token>", "verify_email_token_page", "build_verify_email_token_page"),
	PageRoute("/audiobookshelf-registration", "audiobookshelf_registration_page", "build_audiobookshelf_registration_page"),
	PageRoute("/api-access-application", "api_access_application_page", "build_api_access_application_page", access="admin"),
	PageRoute("/discord-webhook-registration", "discord_webhook_registration_page", "build_discord_webhook_registration_page"),
	PageRoute("/discord-webhook/verify", "discord_webhook_verify_page", "build_discord_webhook_verify_page"),
	PageRoute("/discord-webhook/verified", "discord_webhook_verified_page", "build_discord_webhook_verified_page"),
	PageRoute("/token", "discord_webhook_token_page", "build_discord_webhook_verify_page"),
	PageRoute("/minecraft", "minecraft_page", "build_minecraft_page"),
	PageRoute("/psql-interface", "psql_interface_page", "build_psql_interface_page", access="admin"),
	PageRoute("/admin", "admin_dashboard_page", "build_admin_dashboard_page", access="admin"),
	PageRoute("/admin/audiobookshelf-approvals", "admin_audiobookshelf_approvals_page", "build_admin_audiobookshelf_approvals_page", access="admin"),
	PageRoute("/admin/discord-webhook-approvals", "admin_discord_webhook_approvals_page", "build_admin_discord_webhook_approvals_page", access="admin"),
	PageRoute("/admin/minecraft-approvals", "admin_minecraft_approvals_page", "build_admin_minecraft_approvals_page", access="admin"),
	PageRoute("/admin/api-access-approvals", "admin_api_access_approvals_page", "build_admin_api_access_approvals_page", access="admin"),
	PageRoute("/admin/email-debug", "admin_email_debug_page", "build_admin_email_debug_page", access="admin"),
	PageRoute("/admin/frontend-test", "admin_frontend_test_page", "build_admin_frontend_test_page", access="admin"),
	PageRoute("/integration/remove", "integration_remove_page", "build_integration_remove_page"),
	PageRoute("/integration/removed", "integration_removed_page", "build_integration_removed_page"),
	PageRoute("/admin/users", "admin_users_page", "build_admin_users_page", access="admin"),
)

for _spec in _PAGE_ROUTES:
	_register_page_route(_spec)


@main.before_app_request
def _timing_start():
	g._t0 = time.perf_counter()


@main.before_request
def load_user():
	path = flask.request.path or ""
	if path == "/api" or path.startswith("/api/"):
		return
	g.user = None
	auth_token = flask.request.cookies.get(AUTH_TOKEN_NAME)
	if auth_token:
		user = UserManagement.get_user_by_session_token(auth_token)
		if user:
			g.user = user
		else:
			logging.info("Invalid session token provided.")


def _ensure_user_loaded_for_error():
	if hasattr(g, "user"):
		return g.user

	token = flask.request.cookies.get(AUTH_TOKEN_NAME)
	if not token:
		g.user = None
		return None

	user = UserManagement.get_user_by_session_token(token)
	g.user = user
	return user


@main.after_app_request
def _timing_end(resp):
	if not hasattr(g, "_t0"):
		return resp
	if resp.is_streamed:
		return resp

	content_type = (resp.headers.get("Content-Type", "") or "").lower()
	if "text/html" not in content_type:
		return resp
	content_encoding = (resp.headers.get("Content-Encoding", "") or "").lower()
	if content_encoding and content_encoding != "identity":
		return resp

	total_ms = (time.perf_counter() - g._t0) * 1000.0
	body = resp.get_data(as_text=True)
	body = body.replace("__BUILD_MS__", f"{total_ms:.1f} ms")
	body = body.replace("__BUILD_MS_CACHED__", f"{total_ms:.1f} ms (cached)")
	resp.set_data(body)
	return resp


@main.route("/")
def landing_page():
	if g.user:
		return flask.redirect("/profile")
	return page_builders.build_empty_landing_page(g.user)


@main.route("/logout")
def logout_page():
	resp = flask.make_response(flask.redirect("/"))
	resp.set_cookie(
		key=AUTH_TOKEN_NAME,
		**session_cookie_kwargs(value="", max_age=0),
	)
	return resp


@main.route("/reset-password/<token>")
def reset_password_token_page(token):
	_ = token
	return page_builders.build_501_page(g.user)


@main.route("/audiobookshelf", methods=["GET"])
def audiobookshelf_redirect_page():
	target = "https://audiobookshelf.zubekanov.com/"
	try:
		resp = requests.get(target, timeout=2.0)
		if resp.status_code < 500:
			return flask.redirect(target)
		status_note = f"HTTP {resp.status_code}"
	except Exception as exc:
		status_note = str(exc) or "Connection failed."
	return page_builders.build_audiobookshelf_unavailable_page(g.user, status_note)


@main.route("/admin/amp", methods=["GET"])
@main.route("/admin/amp/", methods=["GET"])
@page_access("admin")
def admin_amp_redirect_page():
	return flask.redirect("https://amp-panel.zubekanov.com/")


@main.route("/popugame")
def popugame_page():
	return page_builders.build_popugame_landing_page(g.user)


@main.route("/popugame/local")
def popugame_local_page():
	return page_builders.build_popugame_page(g.user)


@main.route("/popugame/invalid")
def popugame_invalid_page():
	return page_builders.build_popugame_invalid_link_page(g.user), 404


@main.route("/popugame/replay/<code>")
def popugame_replay_page(code: str):
	code = (code or "").strip()
	if not code.isalnum() or len(code) != 6:
		return flask.redirect("/popugame/invalid")
	return page_builders.build_popugame_replay_page(g.user, code=code)


@main.route("/popugame/<code>")
def popugame_game_page(code: str):
	code = (code or "").strip()
	if not code.isalnum() or len(code) != 6:
		return flask.redirect("/popugame/invalid")
	return page_builders.build_popugame_page(g.user, game_code=code)


@main.app_errorhandler(Exception)
def handle_all_errors(e):
	user = _ensure_user_loaded_for_error()

	if isinstance(e, HTTPException):
		return page_builders.build_error_page(user, e), e.code

	tb_text = "".join(traceback.format_exception(type(e), e, e.__traceback__))
	logger.error("Unhandled application error: %s", e)
	logger.error("Unhandled application traceback:\n%s", tb_text)
	try:
		from app.api import _ctx
		from app.api_common import notify_moderators
		notify_moderators(
			_ctx,
			"internal_server_error",
			title="Unhandled server error (500)",
			details=[
				f"Path: {flask.request.method} {flask.request.path}",
				f"Exception: {type(e).__name__}: {str(e)[:200]}",
				f"Traceback: {tb_text.splitlines()[-1][:200]}",
			],
			context={"action": "internal_server_error"},
		)
	except Exception:
		pass
	err = HTTPException()
	err.code = 500
	err.description = "Internal Server Error"
	return page_builders.build_error_page(user, err), 500
