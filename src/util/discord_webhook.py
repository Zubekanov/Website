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
from psycopg2 import errors as psycopg2_errors

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
	Backfill one daily uptime report per loop, newest missing date first.
	Terminates when there are no more missing dates before 'today'.
	"""
	tz = ZoneInfo(discord_config.get("TIMEZONE", "Australia/Sydney"))
	today = datetime.datetime.now(tz).date()
	_debug_reports_created = 0

	while True:
		# Pick the latest day in 'uptime' that has no matching report yet
		rows = psql.select_with_join(
			base_table="uptime u",
			columns=["u.epoch_date AS report_date"],
			joins=[("LEFT JOIN", "uptime_reports r", "r.report_date = u.epoch_date")],
			predicates=[("u.epoch_date", "<", today)],
			raw_predicates=["r.report_date IS NULL"],
			order_by=[("u.epoch_date", "DESC")],
			limit=1
		)


		if not rows:
			logging.info(f"Completed startup report check, created {_debug_reports_created} reports.")
			break

		report_date = rows[0]['report_date']  # <-- use the DB date directly

		# Compute that day's [start, end) in the configured timezone
		day_start_dt = datetime.datetime.combine(report_date, datetime.time(0, 0), tzinfo=tz)
		day_end_dt = day_start_dt + datetime.timedelta(days=1)
		report_start = int(day_start_dt.timestamp())
		report_end = int(day_end_dt.timestamp())

		hour_seconds = 3600
		window_start = report_start
		window_end = window_start + hour_seconds

		cumulative_percentage = 0.0
		emoji_sparkline = ""

		while window_start < report_end:
			rows_count = psql.count_rows(
				table="uptime",
				distinct="epoch",
				conditions={"epoch >=": window_start, "epoch <": window_end}
			)

			count = rows_count
			hourly_percentage = count / (hour_seconds // _PING_INTERVAL) if count else 0.0

			if hourly_percentage > _THRESHOLD_ERROR:
				emoji_sparkline += _ERR_EMOJI_X
			elif hourly_percentage > _THRESHOLD_4:
				emoji_sparkline += _SPK_EMOJI_4
			elif hourly_percentage > _THRESHOLD_3:
				emoji_sparkline += _SPK_EMOJI_3
			elif hourly_percentage > _THRESHOLD_2:
				emoji_sparkline += _SPK_EMOJI_2
			else:
				emoji_sparkline += _SPK_EMOJI_1 if count else _SPK_EMOJI_0

			if hourly_percentage > 1:
				logging.warning(f"Hourly percentage {hourly_percentage} exceeds 100% in window {window_start} - {window_end}.")
				hourly_percentage = 1.0

			cumulative_percentage += ((hourly_percentage * 100) / 24)

			window_start += hour_seconds
			window_end += hour_seconds

		psql.insert_row("uptime_reports", {
			"report_date": report_date,
			"created_at": datetime.datetime.now(tz),
			"uptime": round(cumulative_percentage, 2),
			"emoji_sparkline": emoji_sparkline
		})

		_debug_reports_created += 1

def send_unsent_reports():
	rows = psql.get_rows_by_predicates(
		table="uptime_reports",
		columns=["*"],
		predicates=[("sent_at", "IS NULL", None)],
		order_by=[("report_date", "ASC")]
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
			psql.update_rows_by_conditions(
				table="uptime_reports",
				updates={"sent_at": datetime.datetime.now()},
				conditions={"id": report_id},
				lower_limit=1,
				upper_limit=1
			)
			logging.info(f"Sent report for {report_date}.")

def report_check():
	rows = psql.get_rows_by_predicates(
		table="uptime_reports",
		columns=[("max", "report_date", "last_report")]
	)

	last_report_date = None
	if rows and rows[0]["last_report"]:
		last_report_date = rows[0]["last_report"]

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
		rows = psql.get_rows_by_predicates(
			table="uptime",
			columns=[("count_distinct", "epoch", "count")],
			predicates=[
				("epoch", ">=", window_start),
				("epoch", "<", window_end)
			]
		)
		count = rows[0]["count"] if rows else 0

		if not rows or count == 0:
			window_start += hour_seconds
			window_end += hour_seconds
			emoji_sparkline += _SPK_EMOJI_0
			continue

		window_start += hour_seconds
		window_end += hour_seconds

		hourly_percentage = count / (hour_seconds // _PING_INTERVAL)
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
	rows = psql.get_rows_by_predicates(
		table="uptime",
		columns=[("max", "epoch", "latest")]
	)
	latest = rows[0]["latest"] if rows and rows[0]["latest"] is not None else None

	if not rows or latest is None:
		send_discord_message("❗ Server initialised with no previous uptime data.")
		return

	last_epoch = latest

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
				try:
					psql.insert_default_row("uptime")
				except psycopg2_errors.UniqueViolation:
					logging.warning("Duplicate entry in uptime table, ignoring.")
			
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
