import os
from flask import Flask
from app.routes import main
from util.server_metrics import start_server_metrics_thread

def create_app():
	if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
		start_server_metrics_thread()
		
	app = Flask(
		__name__,
		static_folder="../static",
		template_folder="../templates"
		)
	app.register_blueprint(main)
	
	return app
