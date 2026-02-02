from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Iterable

import requests


def _load_kv_config(path: str) -> dict[str, str]:
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


def _find_gmail_conf() -> dict[str, str]:
	try:
		from util.fcr.file_config_reader import FileConfigReader
		fcr = FileConfigReader()
		conf = fcr.find("gmail.conf")
		if isinstance(conf, dict):
			return {str(k): str(v) for k, v in conf.items()}
	except Exception:
		pass

	src_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
	conf_path = os.path.join(src_root, "config", "gmail.conf")
	return _load_kv_config(conf_path)


@dataclass
class GmailSendResult:
	ok: bool
	status_code: int | None
	error: str | None = None
	message_id: str | None = None


class GmailEmailSender:
	def __init__(
		self,
		*,
		client_id: str | None = None,
		client_secret: str | None = None,
		refresh_token: str | None = None,
		sender_email: str | None = None,
		timeout_s: float = 20.0,
	) -> None:
		conf = _find_gmail_conf()
		self._client_id = (client_id or conf.get("GMAIL_CLIENT_ID") or "").strip()
		self._client_secret = (client_secret or conf.get("GMAIL_CLIENT_SECRET") or "").strip()
		self._refresh_token = (refresh_token or conf.get("GMAIL_REFRESH_TOKEN") or "").strip()
		self._sender_email = (sender_email or conf.get("GMAIL_SENDER_EMAIL") or "").strip()
		self._timeout_s = float(timeout_s)

		self._access_token: str | None = None
		self._access_token_expires_at: float | None = None

	def _refresh_access_token(self) -> str:
		if not self._client_id or not self._client_secret or not self._refresh_token:
			raise RuntimeError("Missing Gmail OAuth credentials.")

		resp = requests.post(
			"https://oauth2.googleapis.com/token",
			data={
				"client_id": self._client_id,
				"client_secret": self._client_secret,
				"refresh_token": self._refresh_token,
				"grant_type": "refresh_token",
			},
			timeout=self._timeout_s,
		)
		resp.raise_for_status()
		payload = resp.json()
		token = payload.get("access_token")
		expires_in = int(payload.get("expires_in") or 0)
		if not token:
			raise RuntimeError("No access_token returned from Gmail token endpoint.")
		self._access_token = token
		if expires_in:
			self._access_token_expires_at = time.time() + max(0, expires_in - 60)
		else:
			self._access_token_expires_at = None
		return token

	def _get_access_token(self) -> str:
		if self._access_token and self._access_token_expires_at:
			if time.time() < self._access_token_expires_at:
				return self._access_token
		return self._refresh_access_token()

	def _build_message(
		self,
		*,
		to_addrs: Iterable[str],
		subject: str,
		body_text: str | None = None,
		body_html: str | None = None,
		cc_addrs: Iterable[str] | None = None,
		bcc_addrs: Iterable[str] | None = None,
		reply_to: str | None = None,
		sender_email: str | None = None,
	) -> str:
		sender = sender_email or self._sender_email
		if not sender:
			raise RuntimeError("Missing sender email.")
		if not to_addrs:
			raise RuntimeError("Missing recipient.")

		msg = MIMEMultipart("alternative")
		msg["From"] = sender
		msg["To"] = ", ".join([addr for addr in to_addrs if addr])
		msg["Subject"] = subject or ""
		if cc_addrs:
			msg["Cc"] = ", ".join([addr for addr in cc_addrs if addr])
		if reply_to:
			msg["Reply-To"] = reply_to

		if body_text:
			msg.attach(MIMEText(body_text, "plain", "utf-8"))
		if body_html:
			msg.attach(MIMEText(body_html, "html", "utf-8"))
		if not body_text and not body_html:
			msg.attach(MIMEText("", "plain", "utf-8"))

		raw_bytes = msg.as_bytes()
		return base64.urlsafe_b64encode(raw_bytes).decode("utf-8")

	def send_email(
		self,
		*,
		to_addrs: Iterable[str],
		subject: str,
		body_text: str | None = None,
		body_html: str | None = None,
		cc_addrs: Iterable[str] | None = None,
		bcc_addrs: Iterable[str] | None = None,
		reply_to: str | None = None,
		sender_email: str | None = None,
	) -> GmailSendResult:
		try:
			raw_message = self._build_message(
				to_addrs=to_addrs,
				subject=subject,
				body_text=body_text,
				body_html=body_html,
				cc_addrs=cc_addrs,
				bcc_addrs=bcc_addrs,
				reply_to=reply_to,
				sender_email=sender_email,
			)
		except Exception as exc:
			return GmailSendResult(ok=False, status_code=None, error=str(exc))

		try:
			token = self._get_access_token()
		except Exception as exc:
			return GmailSendResult(ok=False, status_code=None, error=str(exc))

		resp = requests.post(
			"https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
			headers={
				"Authorization": f"Bearer {token}",
				"Content-Type": "application/json",
			},
			json={"raw": raw_message},
			timeout=self._timeout_s,
		)

		if not resp.ok:
			return GmailSendResult(
				ok=False,
				status_code=resp.status_code,
				error=resp.text,
			)

		payload = resp.json()
		return GmailSendResult(
			ok=True,
			status_code=resp.status_code,
			message_id=str(payload.get("id") or ""),
		)


_DEFAULT_SENDER: GmailEmailSender | None = None
_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


def get_sender() -> GmailEmailSender:
	global _DEFAULT_SENDER
	if _DEFAULT_SENDER is None:
		_DEFAULT_SENDER = GmailEmailSender()
	return _DEFAULT_SENDER


def render_template(name: str, context: dict[str, str]) -> str:
	path = os.path.join(_TEMPLATE_DIR, name)
	with open(path, "r", encoding="utf-8") as handle:
		content = handle.read()
	for key, value in context.items():
		content = content.replace(f"{{{{{key}}}}}", value)
	return content


def send_email(
	*,
	to_addrs: Iterable[str],
	subject: str,
	body_text: str | None = None,
	body_html: str | None = None,
	cc_addrs: Iterable[str] | None = None,
	bcc_addrs: Iterable[str] | None = None,
	reply_to: str | None = None,
	sender_email: str | None = None,
) -> GmailSendResult:
	return get_sender().send_email(
		to_addrs=to_addrs,
		subject=subject,
		body_text=body_text,
		body_html=body_html,
		cc_addrs=cc_addrs,
		bcc_addrs=bcc_addrs,
		reply_to=reply_to,
		sender_email=sender_email,
	)
