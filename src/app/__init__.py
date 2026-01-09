import logging
import re
import sys
import threading
from flask import Flask
from .api import api
from .routes import main
from .resources import resources

from sql.psql_interface import PSQLInterface

_prefix_re = re.compile(r'^(?P<prefix>.+?\])\s+"(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+HTTP/\d\.\d"\s+(?P<status>\d{3})\b')
psql = PSQLInterface()

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
			# Not an access log line
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

	# prevent double-wiring (reloader)
	if getattr(root, "_live_rewrite_configured", False):
		return
	root._live_rewrite_configured = True

	root.handlers.clear()
	root.addHandler(DevLiveRewriteHandler(dev_enabled=dev_enabled))

def create_app():
	app = Flask(__name__)
	app.register_blueprint(main)
	app.register_blueprint(api)
	app.register_blueprint(resources)

	setup_logging(dev_enabled=True)

	psql.verify_tables()

	return app
