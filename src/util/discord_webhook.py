import time
import requests
import datetime
import threading
from dateutil.relativedelta import relativedelta
from util.config_reader import ConfigReader
from util.psql_manager import PSQLClient
from util.lock_manager import get_lock, get_lock_file_path
import logging
from zoneinfo import ZoneInfo

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

_DAY_SECONDS = 24 * 60 * 60
_DAILY_PINGS = _DAY_SECONDS // _PING_INTERVAL

_THRESHOLD_2 = 0.50
_THRESHOLD_3 = 0.90
_THRESHOLD_4 = 0.98
_THRESHOLD_ERROR = 1.0
_SPK_EMOJI_0 = "⬛"
_SPK_EMOJI_1 = "🟥"
_SPK_EMOJI_2 = "🟧"
_SPK_EMOJI_3 = "🟨"
_SPK_EMOJI_4 = "🟩"
_ERR_EMOJI_X = "❌"

_X_AXIS = "`12      03      06      09      12      03      06      09      `\n`AM      AM      AM      AM      PM      PM      PM      PM      `"


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

def startup_report_check():
	"""
	Check the database for days with uptime data and no logged reports.
	"""
	tz = ZoneInfo(discord_config.get("TIMEZONE", "Australia/Sydney"))
	today = datetime.datetime.now(tz).date()
	midnight = (
		datetime.datetime.combine(today, datetime.time(0, 0))
		.replace(tzinfo=tz)
		.timestamp()
	)
	_debug_reports_created = 0
	while True:
		rows = psql.execute(
			"""
			SELECT * FROM uptime u WHERE
			NOT EXISTS (
				SELECT 1 FROM uptime_reports r
				WHERE r.report_date = u.epoch_date
			)
			AND u.epoch_date < %s
			ORDER BY u.epoch DESC
			LIMIT 1;
			""",
			(today, )
		)
		if not rows or rows[0].get("epoch") is None:
			logging.info(f"Completed startup report check, created {_debug_reports_created} reports.")
			break
		last_epoch = rows[0].get("epoch")
		days_since = (int(time.time()) - last_epoch) // _DAY_SECONDS
		report_end = midnight - (days_since * _DAY_SECONDS)
		report_start = report_end - _DAY_SECONDS
		report_date = datetime.datetime.fromtimestamp(report_start, tz).date()
		
		hour_seconds = 3600
		window_start = report_start
		window_end = window_start + hour_seconds

		cumulative_percentage = 0.0
		emoji_sparkline = ""

		while window_start < report_end:
			rows = psql.execute(
				"SELECT COUNT(DISTINCT epoch) AS count FROM uptime WHERE epoch >= %s AND epoch < %s;",
				(window_start, window_end)
			)
			if not rows or rows[0]['count'] == 0:
				window_start += hour_seconds
				window_end += hour_seconds
				emoji_sparkline += _SPK_EMOJI_0
				continue

			window_start += hour_seconds
			window_end += hour_seconds

			hourly_percentage = rows[0]['count'] / (hour_seconds // _PING_INTERVAL)
			if hourly_percentage > _THRESHOLD_ERROR:
				emoji_sparkline += _ERR_EMOJI_X
			elif hourly_percentage > _THRESHOLD_4:
				emoji_sparkline += _SPK_EMOJI_4
			elif hourly_percentage > _THRESHOLD_3:	
				emoji_sparkline += _SPK_EMOJI_3
			elif hourly_percentage > _THRESHOLD_2:
				emoji_sparkline += _SPK_EMOJI_2
			else:
				emoji_sparkline += _SPK_EMOJI_1

			if hourly_percentage > 1:
				logging.warning(
					f"Hourly percentage {hourly_percentage} exceeds 100% in window {window_start} - {window_end}. ")
				hourly_percentage = 1.0

			cumulative_percentage += ((hourly_percentage * 100) / 24)

			logging.debug(
				f"Window {window_start} - {window_end}: "
				f"{rows[0]['count']} pings, "
				f"{hourly_percentage * 100:.2f}% uptime"
			)
		
		logging.debug(
			f"Report for {report_date}: "
			f"{cumulative_percentage:.2f}% cumulative uptime, "
			f"emoji sparkline: {emoji_sparkline}"
		)

		psql.insert_row("uptime_reports", {
			"report_date": report_date,
			"created_at": datetime.datetime.now(),
			"uptime": round(cumulative_percentage, 2),
			"emoji_sparkline": emoji_sparkline
		})

		midnight = report_start
		_debug_reports_created += 1
	
def send_unsent_reports():
	rows = psql.execute(
		"SELECT * FROM uptime_reports WHERE sent_at IS NULL ORDER BY report_date ASC;"
	)

	if not rows:
		logging.info("No unsent reports found.")
		return

	for row in rows:
		report_date = row['report_date']
		report_epoch = int(datetime.datetime.combine(
			report_date, datetime.time(12, 0, 0),
			tzinfo=ZoneInfo(discord_config.get("TIMEZONE", "Australia/Sydney"))
		).timestamp())
		uptime = row['uptime']
		report_id = row['id']
		emoji_sparkline = row['emoji_sparkline']

		message = f"📊 Uptime Report for <t:{report_epoch}:D>:\nDaily uptime: `{uptime}%`\n{emoji_sparkline}\n{_X_AXIS}"
		if send_discord_message(message):
			psql.execute(
				"UPDATE uptime_reports SET sent_at = %s WHERE id = %s;",
				(datetime.datetime.now(), report_id)
			)
			logging.info(f"Sent report for {report_date}.")

def report_check():
	last_report_date = psql.execute(
		"SELECT MAX(report_date) AS last_report FROM uptime_reports;"
	)
	last_report_date = last_report_date[0]['last_report'] if last_report_date and last_report_date[0]['last_report'] else None
	if not last_report_date:
		return
	next_report_date = last_report_date + datetime.timedelta(days=1)
	next_report_midnight = datetime.datetime.combine(next_report_date, datetime.time(0, 0, 0), tzinfo=ZoneInfo(discord_config.get("TIMEZONE", "Australia/Sydney"))).timestamp()

	if time.time() >= next_report_midnight:
		startup_report_check()
	send_unsent_reports()

def _debug_generate_and_send_todays_report():
	"""
	This function is for debugging purposes only.
	It generates and sends today's report immediately.
	"""
	tz = ZoneInfo(discord_config.get("TIMEZONE", "Australia/Sydney"))
	today = datetime.datetime.now(tz).date()
	midnight = (
		datetime.datetime.combine(today, datetime.time(0, 0))
		.replace(tzinfo=tz)
		.timestamp()
	)
	
	hour_seconds = 3600
	window_start = midnight
	window_end = window_start + hour_seconds

	cumulative_percentage = 0.0
	emoji_sparkline = ""

	while window_start < midnight + _DAY_SECONDS:
		rows = psql.execute(
			"SELECT COUNT(DISTINCT epoch) AS count FROM uptime WHERE epoch >= %s AND epoch < %s;",
			(window_start, window_end)
		)
		if not rows or rows[0]['count'] == 0:
			window_start += hour_seconds
			window_end += hour_seconds
			emoji_sparkline += _SPK_EMOJI_0
			continue

		window_start += hour_seconds
		window_end += hour_seconds

		hourly_percentage = rows[0]['count'] / (hour_seconds // _PING_INTERVAL)
		if hourly_percentage > _THRESHOLD_ERROR:
			emoji_sparkline += _ERR_EMOJI_X
		elif hourly_percentage > _THRESHOLD_4:
			emoji_sparkline += _SPK_EMOJI_4
		elif hourly_percentage > _THRESHOLD_3:	
			emoji_sparkline += _SPK_EMOJI_3
		elif hourly_percentage > _THRESHOLD_2:
			emoji_sparkline += _SPK_EMOJI_2
		else:
			emoji_sparkline += _SPK_EMOJI_1
		
		if hourly_percentage > 1:
			logging.warning(
				f"Hourly percentage {hourly_percentage} exceeds 100% in window {window_start} - {window_end}. "
			)
			hourly_percentage = 1.0

		cumulative_percentage += ((hourly_percentage * 100) / 24)
		logging.debug(
			f"Window {window_start} - {window_end}: "
			f"{rows[0]['count']} pings, "
			f"{hourly_percentage * 100:.2f}% uptime"
		)
	
	logging.debug(
		f"Debug report: "
		f"{cumulative_percentage:.2f}% cumulative uptime, "
		f"emoji sparkline: {emoji_sparkline}"
	)
	message= f"📊 `DEBUG UPTIME REPORT FOR` <t:{int(midnight)}:D>:\nDaily uptime: `{cumulative_percentage:.2f}%`\n{emoji_sparkline}\n{_X_AXIS}"
	if send_discord_message(message):
		logging.info("Today's debug report sent successfully.")
	else:
		logging.error("Failed to send today's debug report.")

	logging.info(f"Today's report generated with {cumulative_percentage:.2f}% uptime.")

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
	#_debug_generate_and_send_todays_report()
	send_downtime_message()
	startup_report_check()
	send_unsent_reports()

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
					report_check()
				psql.execute("INSERT INTO uptime DEFAULT VALUES;")
			
		if now % 3600 == 0:
			report_check()
			if not waiting:
				logging.info("Hourly report check completed.")
		
		time.sleep(0.5)

def start_discord_webhook_thread():
		global _LOCK
		_LOCK = open(get_lock_file_path(_LOCK_NAME), "w")
		res = get_lock(_LOCK_NAME, _LOCK)
		if not res:
			return
		logging.info(f"Discord webhook thread started with lock '{_LOCK}'")
		threading.Thread(target=run, daemon=True).start()
