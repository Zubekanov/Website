from functools import wraps
import logging
import time
import traceback
import requests
import flask
from flask import g
from werkzeug.exceptions import HTTPException
from util.user_management import UserManagement
from util.webpage_builder.webpage_builder import (
	build_admin_api_access_approvals_page,
	build_admin_audiobookshelf_approvals_page,
	build_admin_dashboard_page,
	build_admin_discord_webhook_approvals_page,
	build_admin_email_debug_page,
	build_admin_frontend_test_page,
	build_admin_minecraft_approvals_page,
	build_admin_users_page,
	build_api_access_application_page,
	build_audiobookshelf_registration_page,
	build_audiobookshelf_unavailable_page,
	build_delete_account_page,
	build_discord_webhook_registration_page,
	build_discord_webhook_verify_page,
	build_discord_webhook_verified_page,
	build_empty_landing_page,
	build_error_page,
	build_integration_remove_page,
	build_integration_removed_page,
	build_login_page,
	build_minecraft_page,
	build_popugame_invalid_link_page,
	build_popugame_page,
	build_profile_page,
	build_psql_interface_page,
	build_readme_page,
	build_register_page,
	build_reset_password_page,
	build_server_metrics_page,
	build_verify_email_page,
	build_verify_email_token_page,
	build_501_page,
	_is_admin_user as _builder_is_admin_user,
)

logger = logging.getLogger(__name__)
main = flask.Blueprint("main", __name__)
_AUTH_TOKEN_NAME_ = "session"
PAGE_ACCESS_REQUIREMENTS: dict[str, dict[str, str]] = {}


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
				if not _builder_is_admin_user(user):
					err = HTTPException()
					err.code = 403
					err.description = "Admin access required."
					return build_error_page(user, err), 403
			return fn(*args, **kwargs)

		return _wrapped

	return _decorator

@main.before_app_request
def _timing_start():
	g._t0 = time.perf_counter()

@main.before_request
def load_user():
	path = flask.request.path or ""
	if path == "/api" or path.startswith("/api/"):
		return
	g.user = None
	auth_token = flask.request.cookies.get("session")
	if auth_token:
		user = UserManagement.get_user_by_session_token(auth_token)
		if user:
			g.user = user
		else:
			logging.info("Invalid session token provided.")

def _ensure_user_loaded_for_error():
	if hasattr(g, "user"):
		return g.user

	token = flask.request.cookies.get("session")
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

	ct = (resp.headers.get("Content-Type", "") or "").lower()
	if "text/html" not in ct:
		return resp
	ce = (resp.headers.get("Content-Encoding", "") or "").lower()
	if ce and ce != "identity":
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
	return build_empty_landing_page(g.user)

@main.route("/readme")
def readme_page():
	return build_readme_page(g.user)

@main.route("/server-metrics")
def server_metrics_page():
	return build_server_metrics_page(g.user)

@main.route("/profile")
@page_access("auth", unauth_redirect="/login")
def profile_page():
	if not g.user:
		return flask.redirect("/login")
	return build_profile_page(g.user)

@main.route("/login")
@page_access("anon", auth_redirect="/profile")
def login_page():
	if g.user:
		return flask.redirect("/profile")
	return build_login_page(g.user)

@main.route("/register")
@page_access("anon", auth_redirect="/profile")
def register_page():
	if g.user:
		return flask.redirect("/profile")
	return build_register_page(g.user)

@main.route("/logout")
def logout_page():
	resp = flask.make_response(flask.redirect("/"))
	resp.set_cookie(
		key = _AUTH_TOKEN_NAME_,
		value = "",
		httponly = True,
		secure = True,
		samesite = "Lax",
		max_age = 0,
		path = "/",
	)
	return resp

@main.route("/reset-password")
def reset_password_page():
	return build_reset_password_page(g.user)

@main.route("/reset-password/<token>")
def reset_password_token_page(token):
	return build_501_page(g.user)

@main.route("/delete-account")
@page_access("auth", unauth_redirect="/")
def delete_account_page():
	if not g.user:
		return flask.redirect("/")
	return build_delete_account_page(g.user)

@main.route("/verify-email")
def verify_email_page():
	return build_verify_email_page(g.user)

@main.route("/verify-email/<token>")
def verify_email_token_page(token):
	return build_verify_email_token_page(g.user, token)

@main.route("/audiobookshelf-registration", methods=["GET"])
def audiobookshelf_registration_page():
	return build_audiobookshelf_registration_page(g.user)

@main.route("/api-access-application", methods=["GET"])
@page_access("admin")
def api_access_application_page():
	return build_api_access_application_page(g.user)

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
	return build_audiobookshelf_unavailable_page(g.user, status_note)

@main.route("/admin/amp", methods=["GET"])
@main.route("/admin/amp/", methods=["GET"])
@page_access("admin")
def admin_amp_redirect_page():
	return flask.redirect("http://192.168.1.146:8080/")

@main.route("/discord-webhook-registration", methods=["GET"])
def discord_webhook_registration_page():
	return build_discord_webhook_registration_page(g.user)

@main.route("/discord-webhook/verify", methods=["GET"])
def discord_webhook_verify_page():
	return build_discord_webhook_verify_page(g.user)

@main.route("/discord-webhook/verified", methods=["GET"])
def discord_webhook_verified_page():
	return build_discord_webhook_verified_page(g.user)

@main.route("/token", methods=["GET"])
def discord_webhook_token_page():
	return build_discord_webhook_verify_page(g.user)

@main.route("/minecraft")
def minecraft_page():
	return build_minecraft_page(g.user)

@main.route("/popugame")
def popugame_page():
	return build_popugame_page(g.user)

@main.route("/popugame/invalid")
def popugame_invalid_page():
	return build_popugame_invalid_link_page(g.user), 404

@main.route("/popugame/<code>")
def popugame_game_page(code: str):
	code = (code or "").strip()
	if not code.isalnum() or len(code) != 6:
		return flask.redirect("/popugame/invalid")
	return build_popugame_page(g.user, game_code=code)

@main.route("/psql-interface")
@page_access("admin")
def psql_interface_page():
	return build_psql_interface_page(g.user)

@main.route("/admin")
@page_access("admin")
def admin_dashboard_page():
	return build_admin_dashboard_page(g.user)

@main.route("/admin/audiobookshelf-approvals")
@page_access("admin")
def admin_audiobookshelf_approvals_page():
	return build_admin_audiobookshelf_approvals_page(g.user)

@main.route("/admin/discord-webhook-approvals")
@page_access("admin")
def admin_discord_webhook_approvals_page():
	return build_admin_discord_webhook_approvals_page(g.user)

@main.route("/admin/minecraft-approvals")
@page_access("admin")
def admin_minecraft_approvals_page():
	return build_admin_minecraft_approvals_page(g.user)

@main.route("/admin/api-access-approvals")
@page_access("admin")
def admin_api_access_approvals_page():
	return build_admin_api_access_approvals_page(g.user)

@main.route("/admin/email-debug")
@page_access("admin")
def admin_email_debug_page():
	return build_admin_email_debug_page(g.user)

@main.route("/admin/frontend-test")
@page_access("admin")
def admin_frontend_test_page():
	return build_admin_frontend_test_page(g.user)

@main.route("/integration/remove")
def integration_remove_page():
	token = flask.request.args.get("token") or ""
	return build_integration_remove_page(g.user, token)

@main.route("/integration/removed")
def integration_removed_page():
	return build_integration_removed_page(g.user)


@main.route("/admin/users")
@page_access("admin")
def admin_users_page():
	return build_admin_users_page(g.user)

@main.app_errorhandler(Exception)
def handle_all_errors(e):
	user = _ensure_user_loaded_for_error()

	# HTTP errors
	if isinstance(e, HTTPException):
		return build_error_page(user, e), e.code

	tb_text = "".join(traceback.format_exception(type(e), e, e.__traceback__))
	logger.error("Unhandled application error: %s", e)
	logger.error("Unhandled application traceback:\n%s", tb_text)
	err = HTTPException()
	err.code = 500
	err.description = "Internal Server Error"
	return build_error_page(user, err), 500
