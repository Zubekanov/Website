# src/run.py

import os
from app import create_app
from util.server_metrics import start_server_metrics_thread
from util.discord_webhook import start_discord_webhook_thread

app = create_app()

if __name__ == "__main__":
	app.run(port=5001, debug=True)
