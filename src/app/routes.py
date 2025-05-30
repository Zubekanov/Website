from flask import Blueprint, abort, g, make_response, render_template, request, send_from_directory, current_app
from app.layout_fetcher import LayoutFetcher
from app.breadcrumbs import generate_breadcrumbs
import util.server_metrics as metrics
from app.user_management import UserManagement as users

main = Blueprint('main', __name__)
user_manager = users()

@main.app_context_processor
def inject_user():
	"""
	Inject the current user into the template context.
	"""
	auth_token = getattr(g, 'auth_token', None)
	if auth_token:
		user = user_manager.get_user_by_auth_token(auth_token)
		if user:
			return {
				"username": user["username"],
				"email": user["email"],
				"is_verified": user["is_verified"],
				"is_suspended": user["is_suspended"],
				"created_at": user["created_at"],
				"last_accessed": user["last_accessed"],
			}
		else:
			return {
				"username": None
			}
	else:
		return {
			"username": None
		}
			
@main.route("/favicon.ico")
def favicon():
	"""
	Serve the favicon.ico file.
	"""
	return send_from_directory(current_app.static_folder, 'favicon.ico', mimetype='image/vnd.microsoft.icon')

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
	Handle email verification via token.
	"""
	token = request.args.get("token")
	if not token:
		abort(400, "Missing token")
	result = user_manager.verify_user(token)

	if result:
		components = LayoutFetcher.load_layout("verification_success.json")
	else:
		components = LayoutFetcher.load_layout("verification_failure.json")

	return render_template("main_layout.html", **components)

@main.route("/login", methods=["POST"])
def login():
	data = request.get_json()
	required_fields = ["email", "password"]
	for field in required_fields:
		if field not in data:
			abort(400, "Malformed request, missing required fields.")
	auth_token = user_manager.get_auth_token(
		email=data["email"],
		password=data["password"]
	)
	if auth_token:
		response = make_response("Login successful")
		response.status_code = 200
		response.set_cookie(
			"auth_token",
			auth_token,
			max_age=60*60*24*7,  # 7 days
			httponly=True,
		)
		return response
	else:
		abort(401, "Invalid email or password.")

@main.route("/register", methods=["POST"])
def register():
	data = request.get_json()
	required_fields = ["username", "email", "password"]
	for field in required_fields:
		if field not in data:
			abort(400, "Malformed request, missing required fields.")
	result = user_manager.register_user(
		username=data["username"],
		email=data["email"],
		password=data["password"]
	)
	if result:
		response = make_response(
			"User registered successfully. Please check your email for verification."
		)
		response.status_code = 201
		response.set_cookie(
			"auth_token",
			user_manager.get_auth_token(
				email=data["email"],
				password=data["password"]
			),
			max_age=60*60*24*7,  # 7 days
			httponly=True,
		)
		return response
	else:
		abort(400, f"Username or Email already in use.")

@main.route("/api/uptime")
def api_uptime():
	return {"uptime_seconds": metrics.get_uptime()}

@main.route("/api/static_metrics")
def api_static_metrics():
	return metrics.get_static_metrics()

@main.route("/api/live_metrics")
def api_live_metrics():
	return metrics.get_latest_metrics()

@main.route("/api/hour_metrics")
def api_hour_metrics():
	return metrics.get_last_hour_metrics()

@main.route("/api/compressed_metrics")
def api_compressed_metrics():
	return metrics.get_compressed_metrics()