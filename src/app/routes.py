from http.client import HTTPException
import logging
import time
import requests
from app.api import _AUTH_TOKEN_NAME_
import flask
from flask import g
from util.user_management import UserManagement
from util.webpage_builder.webpage_builder import *
from util.webpage_builder.metrics_builder import *
from bokeh.embed import server_document

logger = logging.getLogger(__name__)
main = flask.Blueprint("main", __name__)

@main.before_app_request
def _timing_start():
	g._t0 = time.perf_counter()

@main.before_request
def load_user():
	if flask.request.path.startswith("/api"):
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

	ct = resp.headers.get("Content-Type", "")
	if "text/html" not in ct:
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
def profile_page():
	if not g.user:
		return flask.redirect("/login")
	return build_profile_page(g.user)

@main.route("/login")
def login_page():
	if g.user:
		return flask.redirect("/profile")
	return build_login_page(g.user)

@main.route("/register")
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

@main.route("/psql-interface")
def psql_interface_page():
	return build_psql_interface_page(g.user)

@main.route("/admin")
def admin_dashboard_page():
	return build_admin_dashboard_page(g.user)

@main.route("/admin/audiobookshelf-approvals")
def admin_audiobookshelf_approvals_page():
	return build_admin_audiobookshelf_approvals_page(g.user)

@main.route("/admin/discord-webhook-approvals")
def admin_discord_webhook_approvals_page():
	return build_admin_discord_webhook_approvals_page(g.user)

@main.route("/admin/minecraft-approvals")
def admin_minecraft_approvals_page():
	return build_admin_minecraft_approvals_page(g.user)

@main.route("/admin/email-debug")
def admin_email_debug_page():
	return build_admin_email_debug_page(g.user)

@main.route("/integration/remove")
def integration_remove_page():
	token = flask.request.args.get("token") or ""
	return build_integration_remove_page(g.user, token)

@main.route("/integration/removed")
def integration_removed_page():
	return build_integration_removed_page(g.user)


@main.route("/admin/users")
def admin_users_page():
	return build_admin_users_page(g.user)

@main.app_errorhandler(Exception)
def handle_all_errors(e):
	user = _ensure_user_loaded_for_error()

	# HTTP errors
	if isinstance(e, HTTPException):
		return build_error_page(user, e), e.code
