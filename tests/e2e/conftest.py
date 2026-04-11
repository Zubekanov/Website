from __future__ import annotations

import importlib.util
import os
import socket
import sys
import threading
import time
import types
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest
from werkzeug.serving import make_server

from tests.e2e.support import (
	ADMIN_EMAIL,
	ADMIN_PASSWORD,
	E2EState,
	MEMBER_EMAIL,
	MEMBER_PASSWORD,
	SeedHelper,
)


_HAS_PLAYWRIGHT = bool(importlib.util.find_spec("playwright.sync_api")) and bool(importlib.util.find_spec("pytest_playwright"))


PLOTLY_STUB_JS = """
window.Plotly = {
	newPlot: () => Promise.resolve(),
	react: () => Promise.resolve(),
	extendTraces: () => Promise.resolve(),
	purge: () => undefined,
};
"""


def pytest_collection_modifyitems(config, items):
	if _HAS_PLAYWRIGHT:
		return
	skip = pytest.mark.skip(reason="Install playwright and pytest-playwright to run browser e2e tests.")
	for item in items:
		if item.nodeid.startswith("tests/e2e/"):
			item.add_marker(skip)


@dataclass
class LiveServer:
	base_url: str
	interface: Any
	state: E2EState


class _ServerThread(threading.Thread):
	def __init__(self, server):
		super().__init__(daemon=True)
		self._server = server

	def run(self) -> None:
		self._server.serve_forever()

	def shutdown(self) -> None:
		self._server.shutdown()


class _FakeEmailSender:
	def __init__(self, state: E2EState, result_type: type[Any]):
		self._state = state
		self._result_type = result_type

	def send_email(
		self,
		*,
		to_addrs,
		subject: str,
		body_text: str | None = None,
		body_html: str | None = None,
		cc_addrs=None,
		bcc_addrs=None,
		reply_to: str | None = None,
		sender_email: str | None = None,
	):
		self._state.emails.append({
			"to_addrs": list(to_addrs or []),
			"subject": subject,
			"body_text": body_text or "",
			"body_html": body_html or "",
			"reply_to": reply_to or "",
			"sender_email": sender_email or "",
		})
		return self._result_type(ok=True, status_code=200, error=None, message_id="e2e-email")


def _choose_free_port() -> int:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
		sock.bind(("127.0.0.1", 0))
		sock.listen(1)
		return int(sock.getsockname()[1])


def _purge_runtime_modules() -> None:
	for name in list(sys.modules.keys()):
		if (
			name == "app"
			or name.startswith("app.")
			or name == "sql.psql_interface"
			or name.startswith("sql.psql_interface.")
			or name == "util.user_management"
			or name.startswith("util.user_management.")
			or name == "util.webpage_builder.webpage_builder"
			or name.startswith("util.webpage_builder.webpage_builder.")
			or name == "util.webpage_builder.parent_builder"
			or name.startswith("util.webpage_builder.parent_builder.")
			or name == "util.webpage_builder.metrics_builder"
			or name.startswith("util.webpage_builder.metrics_builder.")
		):
			sys.modules.pop(name, None)


def _install_minecraft_stub(patch: pytest.MonkeyPatch) -> None:
	class _FakeJavaServer:
		@staticmethod
		def lookup(_target: str):
			return _FakeJavaServer()

		def status(self):
			return types.SimpleNamespace(
				description="E2E Minecraft Server",
				players=types.SimpleNamespace(online=2, max=20, sample=[]),
				version=types.SimpleNamespace(name="1.20.4"),
			)

	patch.setitem(sys.modules, "mcstatus", types.SimpleNamespace(JavaServer=_FakeJavaServer))


def _configure_browser_context(context: Any, base_url: str) -> None:
	def _route_handler(route, request) -> None:
		url = request.url
		if url.startswith("https://cdn.plot.ly/plotly-2.35.2.min.js"):
			route.fulfill(status=200, body=PLOTLY_STUB_JS, content_type="application/javascript")
			return
		if url.startswith("https://audiobookshelf.zubekanov.com/"):
			route.fulfill(
				status=200,
				body="<html><body><div data-external-page=\"audiobookshelf\">Audiobookshelf</div></body></html>",
				content_type="text/html",
			)
			return
		if url.startswith(base_url) or url.startswith("data:") or url.startswith("about:") or url.startswith("blob:"):
			route.continue_()
			return
		route.abort()

	context.route("**/*", _route_handler)


def _login_storage_state(browser: Any, base_url: str, email: str, password: str) -> dict[str, Any]:
	context = browser.new_context()
	_configure_browser_context(context, base_url)
	page = context.new_page()
	page.goto(f"{base_url}/login", wait_until="domcontentloaded")
	page.locator('input[name="email"]').fill(email)
	page.locator('input[name="password"]').fill(password)
	page.locator('button[data-submit-route="/login"]').click()
	page.wait_for_url(f"{base_url}/profile")
	storage_state = context.storage_state()
	context.close()
	return storage_state


def _wait_for_server_ready(base_url: str, *, timeout_s: float = 10.0) -> None:
	deadline = time.time() + timeout_s
	last_error: Exception | None = None
	while time.time() < deadline:
		try:
			with urllib.request.urlopen(f"{base_url}/login", timeout=1.0) as response:
				if 200 <= response.status < 500:
					return
		except Exception as exc:
			last_error = exc
		time.sleep(0.1)
	raise RuntimeError(f"E2E server did not become ready at {base_url}") from last_error


def _load_kv_file(path: Path) -> dict[str, str]:
	config: dict[str, str] = {}
	for raw_line in path.read_text(encoding="utf-8").splitlines():
		line = raw_line.strip()
		if not line or line.startswith("#") or "=" not in line:
			continue
		key, value = line.split("=", 1)
		config[key.strip()] = value.strip()
	return config


def _load_e2e_db_config() -> dict[str, str] | None:
	config_path = Path(__file__).resolve().parents[2] / "src" / "config" / "website_db_e2e.conf"
	if not config_path.exists():
		return None
	return _load_kv_file(config_path)


@pytest.fixture(scope="session")
def e2e_state() -> E2EState:
	return E2EState()


@pytest.fixture(scope="session")
def e2e_db_env() -> dict[str, str]:
	env_config = {
		"database": (os.environ.get("WEBSITE_DB_DATABASE") or "").strip(),
		"user": (os.environ.get("WEBSITE_DB_USER") or "").strip(),
		"password": (os.environ.get("WEBSITE_DB_PASSWORD") or "").strip(),
		"host": (os.environ.get("WEBSITE_DB_HOST") or "").strip(),
		"port": (os.environ.get("WEBSITE_DB_PORT") or "").strip(),
	}
	if all(env_config[key] for key in ("database", "user", "password")):
		return env_config

	file_config = _load_e2e_db_config()
	if file_config:
		database = (file_config.get("database") or "").strip()
		user = (file_config.get("user") or "").strip()
		password = (file_config.get("password") or "").strip()
		if database and user and password:
			return {
				"database": database,
				"user": user,
				"password": password,
				"host": (file_config.get("host") or "").strip(),
				"port": (file_config.get("port") or "").strip(),
			}

	pytest.skip(
		"E2E Playwright tests require a dedicated Postgres database via env "
		"(WEBSITE_DB_DATABASE, WEBSITE_DB_USER, WEBSITE_DB_PASSWORD) "
		"or src/config/website_db_e2e.conf"
	)


@pytest.fixture(scope="session")
def live_server(e2e_db_env: dict[str, str], e2e_state: E2EState) -> LiveServer:
	if not _HAS_PLAYWRIGHT:
		pytest.skip("Playwright dependencies are not installed.")

	port = _choose_free_port()
	base_url = f"http://127.0.0.1:{port}"
	patch = pytest.MonkeyPatch()
	patch.setenv("PUBLIC_BASE_URL", base_url)
	patch.setenv("AUTH_COOKIE_SECURE", "false")
	patch.setenv("WEBSITE_DB_DATABASE", e2e_db_env["database"])
	patch.setenv("WEBSITE_DB_USER", e2e_db_env["user"])
	patch.setenv("WEBSITE_DB_PASSWORD", e2e_db_env["password"])
	if e2e_db_env.get("host"):
		patch.setenv("WEBSITE_DB_HOST", e2e_db_env["host"])
	else:
		patch.delenv("WEBSITE_DB_HOST", raising=False)
	if e2e_db_env.get("port"):
		patch.setenv("WEBSITE_DB_PORT", e2e_db_env["port"])
	else:
		patch.delenv("WEBSITE_DB_PORT", raising=False)
	if not (os.environ.get("WEBSITE_TOKEN_SECRET") or "").strip():
		patch.setenv("WEBSITE_TOKEN_SECRET", "e2e-test-secret")

	_purge_runtime_modules()
	_install_minecraft_stub(patch)

	from sql.psql_interface import PSQLInterface

	interface = PSQLInterface()
	interface.client.execute_query("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

	import util.integrations.email.email_interface as email_interface
	import util.integrations.discord.webhook_interface as webhook_interface
	import util.webpage_builder.parent_builder as parent_builder
	import app.api_handlers.admin as admin_handlers
	import app.api_handlers.auth as auth_handlers
	import app.api_handlers.integrations as integration_handlers
	import app.api_handlers.minecraft as minecraft_handlers
	import app.api_handlers.metrics as metrics_handlers

	fake_sender = _FakeEmailSender(e2e_state, email_interface.GmailSendResult)
	patch.setattr(email_interface, "_DEFAULT_SENDER", fake_sender)
	patch.setattr(email_interface, "get_sender", lambda: fake_sender)

	def _fake_test_message(self, webhook_url: str, payload: dict[str, Any]):
		e2e_state.webhook_messages.append({
			"webhook_url": webhook_url,
			"payload": payload,
		})
		return webhook_interface.DiscordSendResult(ok=True, status_code=204, error=None)

	def _fake_send_and_record(self, webhook_row: dict[str, Any], payload: dict[str, Any]):
		e2e_state.webhook_events.append({
			"webhook": dict(webhook_row),
			"payload": payload,
		})
		webhook_id = webhook_row.get("id")
		if webhook_id:
			self._record_webhook_result(
				webhook_id=str(webhook_id),
				ok=True,
				status_code=204,
				error=None,
			)
		return webhook_interface.DiscordSendResult(ok=True, status_code=204, error=None)

	patch.setattr(webhook_interface.DiscordWebhookEmitter, "send_test_message", _fake_test_message)
	patch.setattr(webhook_interface.DiscordWebhookEmitter, "_send_and_record", _fake_send_and_record)
	patch.setattr(parent_builder, "_fetch_github_repos", lambda username, limit=6: ([{
		"label": "website-dev",
		"desc": "Deterministic e2e repo card.",
		"href": f"https://github.com/{username}/website-dev",
		"updated_at": "2026-03-07T00:00:00Z",
	}], 1))
	patch.setattr(parent_builder, "fetch_github_repos", lambda username, limit=6: parent_builder._fetch_github_repos(username, limit=limit))
	patch.setattr(admin_handlers, "sync_amp_minecraft_whitelist", lambda *args, **kwargs: {"ok": True, "added": 0, "removed": 0, "errors": []})
	patch.setattr(auth_handlers, "sync_amp_minecraft_whitelist", lambda *args, **kwargs: {"ok": True, "added": 0, "removed": 0, "errors": []})
	patch.setattr(integration_handlers, "sync_amp_minecraft_whitelist", lambda *args, **kwargs: {"ok": True, "added": 0, "removed": 0, "errors": []})
	patch.setattr(
		minecraft_handlers.requests,
		"get",
		lambda *args, **kwargs: types.SimpleNamespace(
			status_code=200,
			content=(
				b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
				b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf"
				b"\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
			),
		),
	)

	metric_names = {
		"cpu_used": "CPU Used",
		"cpu_temp": "CPU Temperature",
		"ram_used": "RAM Used",
		"disk_used": "Disk Used",
		"net_up": "Network Upload",
		"net_dn": "Network Download",
	}
	metric_units = {
		"cpu_used": "%",
		"cpu_temp": "°C",
		"ram_used": "%",
		"disk_used": "%",
		"net_up": "B/s",
		"net_dn": "B/s",
	}
	sample_ts = [
		"2026-03-07T00:00:00Z",
		"2026-03-07T01:00:00Z",
		"2026-03-07T02:00:00Z",
	]
	patch.setattr(metrics_handlers, "_metrics_names_and_units", lambda: (metric_names, metric_units))
	patch.setattr(metrics_handlers, "_get_metrics", lambda metric, num_entries, format_ts: (sample_ts[-num_entries:], [10.0, 20.0, 30.0][-num_entries:]))
	patch.setattr(metrics_handlers, "_get_metrics_bulk", lambda metrics, num_entries, format_ts: (
		sample_ts[-num_entries:],
		{metric: [10.0, 20.0, 30.0][-num_entries:] for metric in metrics},
	))
	patch.setattr(metrics_handlers, "_get_metrics_bucketed", lambda metric, since_dt, bucket_seconds, format_ts: (sample_ts, [12.0, 18.0, 24.0]))
	patch.setattr(metrics_handlers, "_get_latest_metrics_entry", lambda num_entries=1: [{
		"cpu_used": 24.0,
		"cpu_temp": 52.0,
		"ram_used": 41.0,
		"disk_used": 68.0,
		"net_up": 1024,
		"net_dn": 2048,
	}])

	from app import create_app
	import app.routes as app_routes

	def _fake_audiobookshelf_probe(*args, **kwargs):
		mode = e2e_state.audiobookshelf_probe_mode
		if mode == "error":
			raise RuntimeError(e2e_state.audiobookshelf_probe_error or "Connection failed.")
		status_code = e2e_state.audiobookshelf_probe_status
		return types.SimpleNamespace(status_code=status_code)

	patch.setattr(app_routes.requests, "get", _fake_audiobookshelf_probe)

	app = create_app(
		testing=True,
		verify_tables_safe_mode=True,
		run_startup_tasks=False,
		auth_cookie_secure=False,
	)
	server = make_server("127.0.0.1", port, app, threaded=True)
	thread = _ServerThread(server)
	thread.start()
	_wait_for_server_ready(base_url)

	try:
		yield LiveServer(base_url=base_url, interface=interface, state=e2e_state)
	finally:
		thread.shutdown()
		thread.join(timeout=5)
		interface.client.close()
		patch.undo()


@pytest.fixture
def e2e_seed(live_server: LiveServer) -> SeedHelper:
	helper = SeedHelper(live_server.interface, live_server.state)
	helper.reset_db()
	helper.seed_baseline()
	return helper


@pytest.fixture(scope="session")
def base_url(live_server: LiveServer) -> str:
	return live_server.base_url


@pytest.fixture
def context_factory(browser: Any, browser_name: str, base_url: str, e2e_seed: SeedHelper) -> Callable[[str], Any]:
	if browser_name != "chromium":
		pytest.skip("The browser suite is Chromium-only in v1.")

	contexts: list[Any] = []

	def _make_context(role: str = "anonymous") -> Any:
		if role == "anonymous":
			context = browser.new_context()
			_configure_browser_context(context, base_url)
			contexts.append(context)
			return context

		if role == "member":
			storage_state = _login_storage_state(browser, base_url, MEMBER_EMAIL, MEMBER_PASSWORD)
		elif role == "admin":
			storage_state = _login_storage_state(browser, base_url, ADMIN_EMAIL, ADMIN_PASSWORD)
		else:
			raise ValueError(f"Unsupported browser role: {role}")

		context = browser.new_context(storage_state=storage_state)
		_configure_browser_context(context, base_url)
		contexts.append(context)
		return context

	yield _make_context

	for context in reversed(contexts):
		context.close()


@pytest.fixture
def anon_page(context_factory: Callable[[str], Any]) -> Any:
	context = context_factory("anonymous")
	return context.new_page()


@pytest.fixture
def member_page(context_factory: Callable[[str], Any]) -> Any:
	context = context_factory("member")
	return context.new_page()


@pytest.fixture
def admin_page(context_factory: Callable[[str], Any]) -> Any:
	context = context_factory("admin")
	return context.new_page()
