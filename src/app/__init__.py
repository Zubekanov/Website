import logging
import os
import re
import sys
import threading
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

_prefix_re = re.compile(r'^(?P<prefix>.+?\])\s+"(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+HTTP/\d\.\d"\s+(?P<status>\d{3})\b')

class DevLiveRewriteHandler(logging.Handler):
	def __init__(self, dev_enabled: bool, stream=None, width: int = 120):
		super().__init__()
		self.stream = stream or sys.stdout
		self.dev_enabled = dev_enabled and getattr(self.stream, "isatty", lambda: False)()
		self.width = width

		self._lock = threading.Lock()
		self._count = 0
		self._active = False

	def emit(self, record: logging.LogRecord):
		msg = record.getMessage()

		if not self.dev_enabled:
			self.stream.write(msg + "\n")
			self.stream.flush()
			return

		m = _prefix_re.match(msg)
		if not m:
			self._newline_if_active()
			self.stream.write(msg + "\n")
			self.stream.flush()
			return

		path = m.group("path")
		if path != "/api/metrics/update":
			self._newline_if_active()
			self.stream.write(msg + "\n")
			self.stream.flush()
			return

		with self._lock:
			self._count += 1

			prefix = m.group("prefix")
			method = m.group("method")
			status = m.group("status")

			line = f'{prefix} "{method} {path} HTTP/1.1" {status} - (x {self._count})'
			self.stream.write("\r" + line.ljust(self.width))
			self.stream.flush()
			self._active = True

	def _newline_if_active(self):
		with self._lock:
			if self._active:
				self.stream.write("\n")
				self.stream.flush()
				self._active = False
				self._count = 0

def setup_logging(dev_enabled: bool):
	root = logging.getLogger()
	root.setLevel(logging.INFO)

	if getattr(root, "_live_rewrite_configured", False):
		return
	root._live_rewrite_configured = True

	root.handlers.clear()
	root.addHandler(DevLiveRewriteHandler(dev_enabled=dev_enabled))

def _env_bool(name: str, *, default: bool) -> bool:
	value = os.environ.get(name)
	if value is None:
		return default
	normalized = value.strip().lower()
	if normalized in {"1", "true", "yes", "on"}:
		return True
	if normalized in {"0", "false", "no", "off"}:
		return False
	return default


def create_app(
	*,
	testing: bool = False,
	verify_tables_safe_mode: bool | None = None,
	run_startup_tasks: bool = True,
	auth_cookie_secure: bool | None = None,
):
	from .api import api
	from .routes import main
	from .resources import resources
	from sql.psql_interface import PSQLInterface
	from util.integrations.discord.webhook_interface import ensure_event_keys
	from util.integrations.minecraft.sync_service import startup_reconcile_amp_minecraft_whitelist

	app = Flask(__name__)
	if verify_tables_safe_mode is None:
		verify_tables_safe_mode = bool(testing)
	if auth_cookie_secure is None:
		auth_cookie_secure = _env_bool("AUTH_COOKIE_SECURE", default=not testing)

	app.config.update(
		TESTING=bool(testing),
		AUTH_COOKIE_SECURE=bool(auth_cookie_secure),
		RUN_STARTUP_TASKS=bool(run_startup_tasks),
		VERIFY_TABLES_SAFE_MODE=bool(verify_tables_safe_mode),
		UPLOAD_FOLDER="/HDD01/website_files",
		MAX_CONTENT_LENGTH=20 * 1024 * 1024 * 1024,  # 20 GB per request
	)
	app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
	app.register_blueprint(main)
	app.register_blueprint(api)
	app.register_blueprint(resources)

	setup_logging(dev_enabled=not testing)

	psql = PSQLInterface()
	psql.verify_tables(safe_mode=bool(verify_tables_safe_mode))

	from app.api_handlers.bonsai import init_bonsai
	init_bonsai()

	if run_startup_tasks:
		ensure_event_keys(psql)
		try:
			startup_reconcile_amp_minecraft_whitelist(psql)
		except Exception:
			logging.getLogger(__name__).exception("AMP whitelist startup reconcile failed.")

	return app
