# src/run.py

import os
from app import create_app

def create_application():
	app = create_app()
	if os.getenv("FLASK_ENV") == "development":
		app.config["DEBUG"] = True
	else:
		app.config["DEBUG"] = False
	return app

app = create_application()

if __name__ == "__main__":
	port = int(os.getenv("PORT", 5001))
	app.run(host="0.0.0.0", port=port)
