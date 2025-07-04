import time
import requests
from datetime import datetime, timedelta, timezone
from util.config_reader import ConfigReader
from util.psql_manager import PSQLClient
import logging

logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

discord_config = ConfigReader.get_key_value_config("discord.config")
WEBHOOK_URL = discord_config.get("DISCORD_WEBHOOK_URL")
PING_URL = discord_config.get("WEBSITE_PING_URL", "https://zubekanov.com/api/ping")

PING_INTERVAL = 5
RETRY_SLEEP = 60

psql = PSQLClient()


def log_ping(now_utc=None):
	ts = now_utc or datetime.now(timezone.utc)
	psql.insert_row("uptime_log", {"timestamp": ts})


def send_discord_message(content: str):
	payload = {"username": "zubekanov.com", "content": content}
	try:
		resp = requests.post(WEBHOOK_URL, json=payload)
		resp.raise_for_status()
	except Exception as e:
		print(f"Discord error (ignored): {e}")


def calculate_and_send_daily_report(date_obj):
	exists = psql.execute(
		"SELECT 1 FROM daily_uptime WHERE date = %s LIMIT 1", [date_obj]
	)
	if exists:
		return

	start = datetime.combine(date_obj, datetime.min.time(), tzinfo=timezone.utc)
	end = start + timedelta(days=1)

	query = """
		SELECT COUNT(*) AS ping_count
		FROM uptime_log
		WHERE timestamp >= %s AND timestamp < %s;
	"""
	rows = psql.execute(query, [start, end])
	ping_count = rows[0]["ping_count"]
	seconds_up = ping_count * PING_INTERVAL

	psql.execute(
		"""
		INSERT INTO daily_uptime(date, seconds_up)
		VALUES(%s, %s)
		ON CONFLICT (date) DO UPDATE SET seconds_up = EXCLUDED.seconds_up;
		""", [date_obj, seconds_up]
	)

	percent = round((seconds_up / 86400) * 100, 2)
	hours = seconds_up / 3600
	message = (
		f"📊 Uptime for {date_obj.isoformat()}:\n"
		f"✅ {percent}% up ({hours:.1f} hours)"
	)
	send_discord_message(message)
	
def run():
	rows = psql.execute("SELECT MAX(timestamp) AS last_ping FROM uptime_log", [])
	last_ping_ts = rows[0]["last_ping"]

	now_utc = datetime.now(timezone.utc)
	if last_ping_ts is None:
		first_ever_success = True
		last_failure_time = None
	else:
		gap = now_utc - last_ping_ts
		if gap.total_seconds() > (PING_INTERVAL * 2):
			last_failure_time = last_ping_ts
		else:
			last_failure_time = None
		first_ever_success = False

	rows = psql.execute("SELECT MAX(date) AS last_date FROM daily_uptime", [])
	last_reported_date = rows[0]["last_date"]

	while True:
		now_utc = datetime.now(timezone.utc)
		try:
			resp = requests.get(PING_URL, timeout=3)
			resp.raise_for_status()

			log_ping(now_utc)

			if last_failure_time:
				duration = now_utc - last_failure_time
				total_secs = int(duration.total_seconds())
				hours, rem = divmod(total_secs, 3600)
				minutes, seconds = divmod(rem, 60)

				if hours:
					offline_detail = f"offline for {hours}h {minutes}m {seconds}s"
				else:
					offline_detail = f"offline for {minutes}m {seconds}s"

				if first_ever_success:
					detail = f"(first known state; {offline_detail})"
				else:
					detail = f"({offline_detail})"

				readable = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
				send_discord_message(f"❗ Site online at {readable} {detail}")

				first_ever_success = False
				last_failure_time = None

			elif first_ever_success:
				readable = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
				send_discord_message(f"❗ Site online at {readable} (first known state)")
				first_ever_success = False

			today = now_utc.date()
			if last_reported_date and today > last_reported_date:
				calculate_and_send_daily_report(last_reported_date)
				last_reported_date = today

			time.sleep(PING_INTERVAL)

		except Exception as e:
			print(f"Ping failed: {e}")
			if last_failure_time is None:
				last_failure_time = datetime.now(timezone.utc)
			time.sleep(RETRY_SLEEP)

def start_discord_webhook_thread():
	import threading
	thread = threading.Thread(target=run, daemon=True)
	thread.start()
	print("Discord webhook thread started.")
	return thread
