# routes.py
import logging
from flask import (
	Blueprint, abort, g, make_response, jsonify,
	render_template, request, send_from_directory, current_app, url_for
)
from app.layout_fetcher import LayoutFetcher
from app.breadcrumbs import generate_breadcrumbs
import util.server_metrics as metrics
from app.user_management import UserManagement as users
from util.http_error_checker import validate

logger = logging.getLogger(__name__)
main = Blueprint('main', __name__)
user_manager = users()

@main.app_context_processor
def inject_user():
	"""
	Inject current_user into Jinja context (None if not logged in).
	"""
	current = getattr(g, "user", None)
	if current:
		current = {
			"username": current.get("username"),
			"email": current.get("email"),
			"verified": current.get("verified", False)
		}
	else:
		current = {}
	return current

@main.route("/favicon.ico")
def favicon():
	"""
	Serve the favicon.ico from static.
	"""
	return send_from_directory(
		current_app.static_folder, 
		'favicon.ico', 
		mimetype='image/vnd.microsoft.icon'
	)

@main.route("/")
def homepage():
	components = LayoutFetcher.load_layout("homepage.json")
	breadcrumbs = generate_breadcrumbs()
	return render_template("main_layout.html", **components, breadcrumbs=breadcrumbs)

@main.route("/server")
def server_overview():
	components = LayoutFetcher.load_layout("server_overview.json")
	breadcrumbs = generate_breadcrumbs()
	return render_template("main_layout.html", **components, breadcrumbs=breadcrumbs)

@main.route("/verify")
def verify_email():
	"""
	Handle email verification via token, then issue an auth cookie if successful.
	"""
	validation = validate(request.args, required=["token"])
	if validation["error"]:
		abort(validation["response"], validation["message"])
	
	token = validation["request_data"]["token"].strip()

	result = user_manager.verify_user(token)
	if result:
		components = LayoutFetcher.load_layout("verification_success.json")
	else:
		components = LayoutFetcher.load_layout("verification_failure.json")

	# Issue an auth token after successful verification
	auth_token = user_manager.get_verification_auth_token(token)
	if auth_token:
		components["username"] = user_manager.get_user_by_auth_token(auth_token).get("username") if auth_token else None
		logger.debug(f"Generated verification auth token: {auth_token}")
		logger.info(f"Fetched user: {components['username']} for verification token: {token}")
	else:
		logger.error(f"Failed to generate auth token for verification token: {token}")
		auth_token = None

	breadcrumbs = generate_breadcrumbs()
	response = make_response(render_template("main_layout.html", **components, breadcrumbs=breadcrumbs))

	if auth_token:
		response.set_cookie(
			"auth_token",
			auth_token,
			max_age=current_app.config.get("AUTH_TOKEN_TTL", 60*60*24*7),
			httponly=True,
			secure=True,
			samesite="Lax"
		)
		logger.debug(f"User verified; auth token set for token: {token}")
	
	response.status_code = 200 if result else 400

	return response

@main.route("/logout")
def logout():
	"""
	Log out the user by invalidating their auth token and clearing the cookie.
	"""
	user = getattr(g, "user", None)
	if not user:
		abort(400, "No user is currently logged in.")

	# We know there was a token (since g.user is not None), so fetch and invalidate
	token = request.cookies.get("auth_token")
	user_manager.invalidate_auth_token(token)

	response = make_response(jsonify({"message": "Logout successful"}), 200)
	response.set_cookie(
		"auth_token",
		"",
		expires=0,
		httponly=True,
		secure=True,
		samesite="Lax"
	)
	logger.debug(f"User '{user['username']}' logged out; auth token invalidated.")
	return response

@main.route("/login", methods=["POST"])
def login():
	"""
	Accepts JSON {"email": "...", "password": "..."} and returns a new auth_token cookie.
	"""

	validation = validate(request.get_json(silent=True), required=["email", "password"])
	if validation["error"]:
		abort(validation["response"], validation["message"])
	
	data = validation["request_data"]

	email = data["email"].strip()
	password = data["password"]
	
	auth_token = user_manager.get_auth_token(email=email, password=password)
	if not auth_token:
		abort(401, "Invalid email or password.")

	response = make_response(jsonify({"message": "Login successful"}), 200)
	response.set_cookie(
		"auth_token",
		auth_token,
		max_age=current_app.config.get("AUTH_TOKEN_TTL", 60*60*24*7),
		httponly=True,
		secure=True,
		samesite="Lax"
	)
	logger.debug(f"User '{email}' logged in; auth token set.")
	return response

@main.route("/register", methods=["POST"])
def register():
	"""
	Accepts JSON {"username": "...", "email": "...", "password": "..."} to create a new user.
	"""

	validation = validate(request.get_json(silent=True), required=["username", "email", "password"])
	if validation["error"]:
		abort(validation["response"], validation["message"])
	
	data = validation["request_data"]

	username = data["username"].strip()
	email = data["email"].strip()
	password = data["password"]

	logger.debug(f"Received registration data: username={username}, email={email}")

	success = user_manager.register_user(username=username, email=email, password=password)
	if not success:
		abort(400, "Username or email already in use.")

	return make_response(jsonify({
		"message": "User registered successfully. Please check your email for verification."
	}), 201)

@main.route("/forgot-password")
def forgot_password():
	"""
	Render a page where users can request a password reset.
	"""
	components = LayoutFetcher.load_layout("forgot_password.json")
	breadcrumbs = generate_breadcrumbs()
	return render_template("main_layout.html", **components, breadcrumbs=breadcrumbs)

@main.route("/password-reset-request", methods=["POST"])
def password_reset_request():
	"""
	Accepts JSON {"email": "..."} to initiate a password reset.
	"""

	validation = validate(request.get_json(silent=True), required=["email"])
	if validation["error"]:
		abort(validation["response"], validation["message"])
	
	data = validation["request_data"]
	email = data.get("email", "").strip()
	logger.debug(f"Received password reset request for email: {email}")
	user_manager.request_password_reset(email=email)
	
	return make_response(jsonify({
		"message": "If the email is verified, a password reset link has been sent."
	}), 200)

@main.route("/reset-password")
def reset_password():
	"""
	Render the password reset page where users can set a new password.
	"""

	validation = validate(request.args, required=["token"])
	if validation["error"]:
		abort(validation["response"], validation["message"])
	
	# TODO reject invalid/expired tokens here

	components = LayoutFetcher.load_layout("password_reset.json")
	breadcrumbs = generate_breadcrumbs()
	return render_template("main_layout.html", **components, breadcrumbs=breadcrumbs, token=token)

@main.route("/reset-password", methods=["POST"])
def reset_password_submit():
	"""
	Accepts JSON {"token": "...", "new_password": "..."} to reset the user's password.
	"""

	validation = validate(request.get_json(silent=True), required=["token", "new_password"])
	if validation["error"]:
		abort(validation["response"], validation["message"])
	
	data = validation["request_data"]


	token = data.get("token", "").strip()
	new_password = data.get("new_password", "").strip()
	
	if not token or not new_password:
		abort(400, "Token and new password are required.")

	logger.debug(f"Received password reset for token: {token}")

	success = user_manager.reset_password(token=token, new_password=new_password)
	if not success:
		abort(400, "Invalid or expired password reset token.")

	return make_response(jsonify({"message": "Password reset successfully."}), 200)

@main.route("/api/uptime")
def api_uptime():
	return jsonify({"uptime_seconds": metrics.get_uptime()})

@main.route("/api/static_metrics")
def api_static_metrics():
	return jsonify(metrics.get_static_metrics())

@main.route("/api/live_metrics")
def api_live_metrics():
	return jsonify(metrics.get_latest_metrics())

@main.route("/api/timestamp_metrics")
def api_compressed_metrics():
	"""
	Optionally accepts two timestamp query parameters:
		- start: Start timestamp in seconds since epoch (default: 1 hour ago, inclusive)
		- stop: End timestamp in seconds since epoch (default: now, inclusive)
		- step: Step in seconds (default: 5 seconds, steps should be a multiple of 5 but will be rounded up to the nearest 5)
	Attempts to return metrics for the specified time range, but advise paged requests for large ranges.
	"""

	validation = validate(request.args, required=[], optional=["start", "stop", "step"])
	if validation["error"]:
		abort(validation["response"], validation["message"])
	
	data = validation["request_data"]
	start = data.get("start", None)
	if start is not None: start = int(start)
	stop = data.get("stop", None)
	if stop is not None: stop = int(stop)
	step = data.get("step", 5)
 
	# Simple check to prevent ridiculous requests from being processed.
	if (stop - start) / step > 65536:
		abort(400, "Requested range is too large. Please reduce the time range or increase the step size.")

	metric = metrics.get_range_metrics(start=start, stop=stop, step=step)

	return jsonify(metric)
