import os
from flask import Flask, request, g
from app.routes import main
from util.server_metrics import start_server_metrics_thread
from app.user_management import UserManagement as users

user_manager = users()

def create_app():
	if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
		start_server_metrics_thread()
		
	app = Flask(
		__name__,
		static_folder="../static",
		template_folder="../templates"
		)
	app.register_blueprint(main)

	@app.before_request
	def load_auth_token():
		"""
		Before each request, check for the auth token in cookies.
		"""
		auth_token = request.cookies.get("auth_token")
		if not auth_token:
			print("[DEBUG] No auth token found in cookies.")
			return None
		else:
			g.auth_token = auth_token
			g.clear_token = False
			user = user_manager.get_user_by_auth_token(auth_token)
			if not user:
				g.auth_token = None
				g.clear_token = True
				print("[DEBUG] Invalid auth token found in cookies.")
				return None
			print(f"[DEBUG] Auth token found: {auth_token}")
			print(f"[DEBUG] Linked to user: {user['username']}")

	@app.after_request
	def clear_auth_token(response):
		"""
		After each request, clear the auth token if it was invalid.
		This is a placeholder for actual authentication logic.
		"""
		if getattr(g, 'clear_token', False):
			response.set_cookie("auth_token", "", expires=0)
			print("[DEBUG] Invalid auth token cleared from cookies.")
		return response
	
	return app
