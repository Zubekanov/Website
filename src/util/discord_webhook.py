import time
import requests
from datetime import datetime, timedelta
from util.config_reader import ConfigReader
from util.psql_manager import PSQLClient

discord_config = ConfigReader.get_key_value_config("discord.config")
WEBHOOK_URL = discord_config.get("DISCORD_WEBHOOK_URL")
PING_URL = discord_config.get("WEBSITE_PING_URL", "https://zubekanov.com/api/ping")

PING_INTERVAL = 5
RETRY_SLEEP = 60

psql = PSQLClient()

def log_ping():
	psql.insert_row("uptime_log", {"timestamp": datetime.utcnow()})

def send_discord_message(content: str):
	payload = {"username": "UptimeBot", "content": content}
	try:
		resp = requests.post(WEBHOOK_URL, json=payload)
		resp.raise_for_status()
	except Exception as e:
		print(f"Discord error: {e}")
		raise

def calculate_and_send_daily_report(date):
	start = datetime.combine(date, datetime.min.time())
	end = start + timedelta(days=1)

	query = """
		SELECT COUNT(*) AS ping_count
		FROM uptime_log
		WHERE timestamp >= %s AND timestamp < %s;
	"""
	rows = psql.execute(query, [start, end])
	ping_count = rows[0]["ping_count"]
	seconds_up = ping_count * PING_INTERVAL

	psql.insert_row("daily_uptime", {
		"date": date,
		"seconds_up": seconds_up
	})

	percent = round((seconds_up / 86400) * 100, 2)
	message = f"📊 Uptime for {date}:\n✅ {percent}% up ({seconds_up / 3600:.1f} hours)"
	send_discord_message(message)

def run():
	last_day = datetime.utcnow().date()
	first_success = True

	while True:
		now = datetime.utcnow()
		try:
			resp = requests.get(PING_URL, timeout=3)
			if resp.status_code == 200:
				log_ping()

				if first_success or now.date() != last_day:
					if not first_success:
						calculate_and_send_daily_report(last_day)

					send_discord_message(f"✅ Site online at {now.isoformat()}")
					last_day = now.date()
					first_success = False

				time.sleep(PING_INTERVAL)
			else:
				raise Exception(f"Non-200: {resp.status_code}")
		except Exception as e:
			print(f"Ping failed: {e}")
			first_success = True
			time.sleep(RETRY_SLEEP)
		
def start_discord_webhook_thread():
	import threading
	thread = threading.Thread(target=run, daemon=True)
	thread.start()
	print("Discord webhook thread started.")
	return thread
