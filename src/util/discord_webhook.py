import time
import requests
import datetime
import threading
from dateutil.relativedelta import relativedelta
from util.config_reader import ConfigReader
from util.psql_manager import PSQLClient
from util.lock_manager import get_lock, get_lock_file_path
import logging

logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

discord_config = ConfigReader.get_key_value_config("discord.config")
WEBHOOK_URL = discord_config.get("DISCORD_WEBHOOK_URL")
PING_URL = discord_config.get("WEBSITE_PING_URL", "https://zubekanov.com/api/ping")

_LOCK_NAME = "discord_webhook_lock"
_LOCK = None

_PING_INTERVAL = 5
_WAIT_THRESHOLD = 12
_RETRY_SLEEP = 60
_DOWN_THRESHOLD = _RETRY_SLEEP * 2
_DOWN_THRESHOLD_STRING = (f"{_DOWN_THRESHOLD // 60} minutes" if _DOWN_THRESHOLD >= 60 else f"{_DOWN_THRESHOLD} seconds")

psql = PSQLClient()

def ping_website():
	try:
		response = requests.get(PING_URL, timeout=10)
		if response.status_code == 200:
			return True
	except requests.RequestException as e:
		logging.error(f"Website ping failed: {e}")
	return False

def ping_204():
	try:
		response = requests.get("https://www.google.com/generate_204", timeout=10)
		if response.status_code == 204:
			return True
	except requests.RequestException as e:
		logging.error(f"Google 204 ping failed: {e}")
	return False

def send_discord_message(contents: str, ping_admin: bool = False):
	time_str = f"<t:{int(time.time())}:t>"
	if ping_admin:
		admin_id = discord_config.get("ADMIN_ID")
		if not admin_id:
			logging.error("Admin ID not configured in discord.config")
		else:
			contents = f"Attention <@{admin_id}>: {contents}"
	payload = {
		"content": f"[{time_str}]: {contents}",
		"username": "zubekanov.com",
	}
	try:
		response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
		if response.status_code != 204:
			logging.error(f"Discord webhook failed with status {response.status_code}: {response.text}")
			return False
		return True
	except requests.RequestException as e:
		logging.error(f"Discord webhook request failed: {e}")
		return False
	
def human_delta(start: datetime, end: datetime) -> str:
	rd = relativedelta(end, start)

	units = [
		(rd.years,   "year",   "years"),
		(rd.months,  "month",  "months"),
		(rd.days,    "day",    "days"),
		(rd.hours,   "hour",   "hours"),
		(rd.minutes, "minute", "minutes"),
		(rd.seconds, "second", "seconds"),
	]

	parts = [(val, singular if val == 1 else plural)
			 for val, singular, plural in units
			 if val]
	if not parts:
		return "0 seconds"

	parts = parts[:2]
	return ", ".join(f"{val} {label}" for val, label in parts)

def send_downtime_message():
	rows = psql.execute("SELECT MAX(epoch) AS latest FROM uptime;")
	
	if not rows or rows[0]['latest'] is None:
		send_discord_message("❗ Server initialised with no previous uptime data.")
		return

	last_epoch = rows[0]['latest']

	now = int(time.time())
	down = now - last_epoch
	delta = human_delta(
		datetime.datetime.fromtimestamp(last_epoch),
		datetime.datetime.fromtimestamp(now)
	)

	if down > _DOWN_THRESHOLD:
		start_tag = f"<t:{last_epoch}:F>"
		send_discord_message(
			f"❗ Server was down for `{delta}` (from {start_tag})."
		)
	else:
		send_discord_message(
			f"❕ Server restarted within downtime threshold (`threshold={_DOWN_THRESHOLD_STRING}, down={down} seconds`)."
		)

def run():
	send_downtime_message()

	last_log = 0
	waiting = False
	dropped_pings = 0
	while True:
		now = int(time.time())
		interval = _PING_INTERVAL if not waiting else _RETRY_SLEEP

		if now % interval == 0 and last_log != now:
			result = ping_website()

			if not result:
				dropped_pings += 1

			if not result and not waiting and dropped_pings >= _WAIT_THRESHOLD:
				if ping_204():
					send_discord_message("❗ Connectivity OK but website is unavailable, please diagnose.", ping_admin=True)
				waiting = True
			last_log = now

			if result:
				dropped_pings = 0
				if waiting:
					waiting = False
					send_downtime_message()
				psql.execute("INSERT INTO uptime DEFAULT VALUES;")
		
		time.sleep(0.5)

def start_discord_webhook_thread():
		global _LOCK
		_LOCK = open(get_lock_file_path(_LOCK_NAME), "w")
		res = get_lock(_LOCK_NAME, _LOCK)
		if not res:
			return
		logging.info(f"Discord webhook thread started with lock '{_LOCK_NAME}'")
		threading.Thread(target=run, daemon=True).start

