import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests


def _load_kv_config(path: str) -> dict[str, str]:
	"""
	Load simple KEY=VALUE pairs, ignoring blank lines and comments.
	"""
	if not os.path.exists(path):
		raise FileNotFoundError(f"Config not found: {path}")

	config: dict[str, str] = {}
	with open(path, "r", encoding="utf-8") as handle:
		for raw in handle:
			line = raw.strip()
			if not line or line.startswith("#"):
				continue
			if "=" not in line:
				continue
			key, value = line.split("=", 1)
			config[key.strip()] = value.strip()
	return config


def _build_auth_url(*, client_id: str, redirect_uri: str, scope: str) -> str:
	params = {
		"client_id": client_id,
		"redirect_uri": redirect_uri,
		"response_type": "code",
		"scope": scope,
		"access_type": "offline",
		"prompt": "consent",
		"include_granted_scopes": "true",
	}
	return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def _exchange_code_for_tokens(*, client_id: str, client_secret: str, redirect_uri: str, code: str) -> dict:
	token_url = "https://oauth2.googleapis.com/token"
	payload = {
		"client_id": client_id,
		"client_secret": client_secret,
		"redirect_uri": redirect_uri,
		"code": code,
		"grant_type": "authorization_code",
	}
	resp = requests.post(token_url, data=payload, timeout=20)
	resp.raise_for_status()
	return resp.json()


class _OAuthHandler(BaseHTTPRequestHandler):
	code: str | None = None
	error: str | None = None

	def log_message(self, format: str, *args) -> None:  # noqa: A002
		# Suppress default request logging.
		return

	def do_GET(self) -> None:  # noqa: N802
		parsed = urllib.parse.urlparse(self.path)
		params = urllib.parse.parse_qs(parsed.query or "")
		code = (params.get("code") or [None])[0]
		error = (params.get("error") or [None])[0]

		self.__class__.code = code
		self.__class__.error = error

		self.send_response(200)
		self.send_header("Content-Type", "text/html; charset=utf-8")
		self.end_headers()

		if code:
			body = "<html><body><h3>Authorization received.</h3>You can close this window.</body></html>"
		elif error:
			body = f"<html><body><h3>Authorization error:</h3><pre>{error}</pre></body></html>"
		else:
			body = "<html><body><h3>No authorization code found.</h3></body></html>"
		self.wfile.write(body.encode("utf-8"))


def _start_local_server(host: str, port: int) -> tuple[HTTPServer, threading.Thread]:
	server = HTTPServer((host, port), _OAuthHandler)
	thread = threading.Thread(target=server.handle_request, daemon=True)
	thread.start()
	return server, thread


def main() -> int:
	root = os.path.dirname(os.path.abspath(__file__))
	conf_path = os.path.join(root, "config", "gmail.conf")

	try:
		conf = _load_kv_config(conf_path)
	except Exception as exc:
		print(str(exc))
		return 1

	client_id = conf.get("GMAIL_CLIENT_ID", "").strip()
	client_secret = conf.get("GMAIL_CLIENT_SECRET", "").strip()
	sender_email = conf.get("GMAIL_SENDER_EMAIL", "").strip()

	if not client_id or not client_secret:
		print("Missing GMAIL_CLIENT_ID or GMAIL_CLIENT_SECRET in gmail.conf")
		return 1

	redirect_uri = os.environ.get("GMAIL_REDIRECT_URI", "http://localhost:8080/oauth2callback")
	scope = os.environ.get("GMAIL_SCOPE", "https://www.googleapis.com/auth/gmail.send")

	parsed_redirect = urllib.parse.urlparse(redirect_uri)
	use_local_server = parsed_redirect.hostname in {"localhost", "127.0.0.1"} and parsed_redirect.port
	server = None

	if use_local_server:
		try:
			server, _ = _start_local_server(parsed_redirect.hostname, parsed_redirect.port)
		except Exception as exc:
			print(f"Failed to start local server on {parsed_redirect.hostname}:{parsed_redirect.port}: {exc}")
			server = None

	print("Open this URL in a browser and authorize the app:")
	print(_build_auth_url(client_id=client_id, redirect_uri=redirect_uri, scope=scope))
	print("")
	if server:
		print("Waiting for redirect on the local server...")
		timeout_s = int(os.environ.get("GMAIL_OAUTH_TIMEOUT", "180"))
		start = time.time()
		code = None
		while time.time() - start < timeout_s:
			if _OAuthHandler.code or _OAuthHandler.error:
				code = _OAuthHandler.code
				break
			time.sleep(0.2)
		if server:
			server.server_close()
	else:
		print("After approving, you'll be redirected to your redirect URI with a `code` parameter.")
		print("Paste the `code` value here (not the whole URL).")
		try:
			code = input("Authorization code: ").strip()
		except KeyboardInterrupt:
			print("")
			return 1

	if _OAuthHandler.error:
		print(f"Authorization error: {_OAuthHandler.error}")
		return 1

	if not code:
		print("No authorization code provided.")
		return 1

	try:
		tokens = _exchange_code_for_tokens(
			client_id=client_id,
			client_secret=client_secret,
			redirect_uri=redirect_uri,
			code=code,
		)
	except Exception as exc:
		print(f"Token exchange failed: {exc}")
		return 1

	refresh_token = tokens.get("refresh_token", "")
	access_token = tokens.get("access_token", "")

	print("")
	print("Gmail OAuth values:")
	print(f"GMAIL_CLIENT_ID={client_id}")
	print(f"GMAIL_CLIENT_SECRET={client_secret}")
	print(f"GMAIL_SENDER_EMAIL={sender_email}")
	print(f"GMAIL_REFRESH_TOKEN={refresh_token}")
	if access_token:
		print(f"GMAIL_ACCESS_TOKEN={access_token}")

	if not refresh_token:
		print("")
		print("Warning: refresh_token was not returned. Re-run and ensure prompt=consent is honored,")
		print("or revoke prior consent for this client and try again.")

	return 0


if __name__ == "__main__":
	sys.exit(main())
