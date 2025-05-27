import base64
import os
from email.message import EmailMessage
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config_reader import ConfigReader


class GmailClient:
	def __init__(self,
		client_secrets: str = "credentials.json",
		token_file: str = "token.json",
		scopes: list[str] = ["https://mail.google.com/"],
		user_id: str = "me"
	):
		# OAuth2 credentials
		self.creds = ConfigReader.get_credentials(
			client_secrets=client_secrets,
			token_file=token_file,
			scopes=scopes
		)
		self.service = build("gmail", "v1", credentials=self.creds)
		self.user_id = user_id
	
	def send_message(self, msg: EmailMessage) -> dict | None:
		"""Send a raw EmailMessage via Gmail API."""
		raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
		body = {"raw": raw}
		try:
			sent = self.service.users().messages().send(
				userId=self.user_id, body=body
			).execute()
			return sent
		except HttpError as error:
			print(f"Failed to send message: {error}")
			return None
	
	def _send_html(self, to_addr: str, subject: str, plain: str, html: str, thread_id: str = None, in_reply_to: str = None):
		msg = EmailMessage()
		msg['To'] = to_addr
		msg['From'] = self._get_alias()
		msg['Subject'] = subject
		# Use actual Message-ID header for threading
		if in_reply_to:
			msg['In-Reply-To'] = in_reply_to
			msg['References'] = in_reply_to
		msg.set_content(plain)
		msg.add_alternative(html, subtype='html')
		raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
		body = {'raw': raw}
		# Instruct Gmail API to append to an existing thread
		if thread_id:
			body['threadId'] = thread_id
		return self.send_message(msg)
	
	def send_email_with_attachments(
		self, recipient: str, subject: str, body: str,
		attachments: list[str], html: bool = False
	) -> dict | None:
		"""Send an email with attachments."""
		msg = EmailMessage()
		if html:
			msg.set_content("Please use an HTML-compatible client.")
			msg.add_alternative(body, subtype="html")
		else:
			msg.set_content(body)
		msg["To"] = recipient
		msg["From"] = self._get_alias()
		msg["Subject"] = subject
		# Attach files
		for path in attachments:
			filename = os.path.basename(path)
			with open(path, "rb") as f:
				data = f.read()
				maintype, subtype = (_guess_mime(path) or ("application", "octet-stream"))
				msg.add_attachment(data,
					maintype=maintype,
					subtype=subtype,
					filename=filename
				)
		return self.send_message(msg)
	
	def list_labels(self) -> list[dict]:
		"""List all labels in the account."""
		results = self.service.users().labels().list(userId=self.user_id).execute()
		return results.get("labels", [])
	
	def create_label(
		self, name: str,
		label_list_visibility: str = "labelShow",
		message_list_visibility: str = "show"
	) -> dict | None:
		"""Create a new label."""
		label_body = {
			"name": name,
			"labelListVisibility": label_list_visibility,
			"messageListVisibility": message_list_visibility
		}
		try:
			label = self.service.users().labels().create(
				userId=self.user_id, body=label_body
			).execute()
			return label
		except HttpError as error:
			print(f"Failed to create label: {error}")
			return None
	
	def list_messages(
		self, query: str = None, label_ids: list[str] = None, max_results: int = 100
	) -> list[dict]:
		"""List message IDs matching query or labels."""
		params: dict = {"userId": self.user_id, "maxResults": max_results}
		if query:
			params["q"] = query
		if label_ids:
			params["labelIds"] = label_ids
		resp = self.service.users().messages().list(**params).execute()
		return resp.get("messages", [])
	
	def get_message(self, msg_id: str, format: str = "full") -> dict:
		"""Get a message by ID."""
		return self.service.users().messages().get(
			userId=self.user_id, id=msg_id, format=format
		).execute()
	
	def delete_message(self, msg_id: str) -> None:
		"""Permanently delete a message."""
		self.service.users().messages().delete(
			userId=self.user_id, id=msg_id
		).execute()
	
	def trash_message(self, msg_id: str) -> None:
		"""Move a message to the Trash."""
		self.service.users().messages().trash(
			userId=self.user_id, id=msg_id
		).execute()
	
	def modify_message_labels(
		self, msg_id: str,
		add_labels: list[str] = None,
		remove_labels: list[str] = None
	) -> dict:
		"""Add or remove labels on a message."""
		body: dict = {}
		if add_labels:
			body["addLabelIds"] = add_labels
		if remove_labels:
			body["removeLabelIds"] = remove_labels
		return self.service.users().messages().modify(
			userId=self.user_id, id=msg_id, body=body
		).execute()
	
	def get_thread(self, thread_id: str, format: str = "full") -> dict:
		"""Get a thread by ID."""
		return self.service.users().threads().get(
			userId=self.user_id, id=thread_id, format=format
		).execute()
	
	def list_threads(
		self, query: str = None, label_ids: list[str] = None, max_results: int = 100
	) -> list[dict]:
		"""List thread IDs matching query or labels."""
		params: dict = {"userId": self.user_id, "maxResults": max_results}
		if query:
			params["q"] = query
		if label_ids:
			params["labelIds"] = label_ids
		resp = self.service.users().threads().list(**params).execute()
		return resp.get("threads", [])
	
	def watch(self, topic_name: str, label_ids: list[str] = ["INBOX"]) -> dict:
		"""Start push notifications to a Pub/Sub topic."""
		body = {"labelIds": label_ids, "topicName": topic_name}
		return self.service.users().watch(userId=self.user_id, body=body).execute()
	
	def stop_watch(self) -> None:
		"""Stop push notifications."""
		self.service.users().stop(userId=self.user_id).execute()
	
	@staticmethod
	def _get_alias() -> str:
		config = ConfigReader.get_key_value_config("email.config")
		return config.get("ALIAS_ADDRESS") or config.get("EMAIL_ADDRESS")


def _guess_mime(path: str) -> tuple[str, str] | None:
	"""Guess MIME type based on file extension."""
	import mimetypes
	mime, _ = mimetypes.guess_type(path)
	if mime:
		return mime.split("/")
	return None

# Example usage
if __name__ == "__main__":
	client = GmailClient()
	print("Labels:", client.list_labels())
	# client.send_email("user@example.com", "Hi", "Hello there!")
