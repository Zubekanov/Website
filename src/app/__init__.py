# __init__.py
import os
import logging
from flask import Flask, request, g, url_for
from app.routes import main
from util.server_metrics import start_server_metrics_thread
from util.discord_webhook import start_discord_webhook_thread
from app.user_management import UserManagement as users

# Configure root logger at module import time (can be customized via app.config later)
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

user_manager = users()

def create_app():
	# Standard Flask application factory
	app = Flask(
		__name__,
		static_folder="../static",
		template_folder="../templates"
	)

	app.register_blueprint(main)


	logger.debug("Starting server metrics thread")
	start_server_metrics_thread()
	logger.debug("Starting Discord webhook thread")
	start_discord_webhook_thread()

	@app.before_request
	def load_auth_token():
		"""
		Before each request, check for the auth token in cookies. If valid, set g.user.
		"""
		token = request.cookies.get("auth_token")
		if not token:
			g.user = None
			g.clear_token = False
			logger.debug("No auth token found in cookies.")
			return

		user = user_manager.get_user_by_auth_token(token)
		if not user:
			# Token was invalid or expired
			g.user = None
			g.clear_token = True
			logger.debug("Invalid or expired auth token found in cookies.")
		else:
			g.user = user
			g.clear_token = False
			logger.debug(f"Auth token valid; user loaded: {user['username']}")

	@app.after_request
	def clear_auth_token(response):
		"""
		After each request, if we flagged clear_token, flush the cookie from the client.
		"""
		if getattr(g, "clear_token", False):
			response.set_cookie(
				"auth_token", 
				"", 
				expires=0, 
				secure=True, 
				httponly=True, 
				samesite="Lax"
			)
			logger.debug("Cleared invalid auth token from cookies.")
		return response

	return app
