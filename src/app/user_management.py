# user_management.py
import uuid
import logging
import datetime
import secrets
from flask import current_app, url_for
from util.config_reader import ConfigReader
from util.gmail_manager import GmailClient
from util.psql_manager import PSQLClient
from argon2 import PasswordHasher
from typing import Optional, Dict

psql = PSQLClient()

# Argon2 hasher parameters
hasher = PasswordHasher(
	time_cost=1,
	memory_cost=64 * 1024,  # 64 MB
	parallelism=8,
	salt_len=16,
	hash_len=32,
)

# Token TTL (in seconds)
AUTH_TOKEN_TTL = 60 * 60 * 24 * 7  # 7 days
VERIFY_TOKEN_TTL = datetime.timedelta(hours=2)

logger = logging.getLogger(__name__)

class UserManagement:
	_instance = None
	_initialised = False

	def __new__(cls):
		if cls._instance is None:
			cls._instance = super(UserManagement, cls).__new__(cls)
		return cls._instance

	def __init__(self):
		if UserManagement._initialised:
			return
		UserManagement._initialised = True

		try:
			schema = ConfigReader.get_sql("schema.sql")
			for stmt in schema:
				psql.execute(query=stmt)
			logger.debug("Database schema initialized successfully.")
		except Exception as e:
			logger.exception("Error initializing database schema: %s", e)
			raise

	def clean_user_data(self) -> None:
		"""
		Remove all expired verification requests and auth tokens from the database.
		"""
		now = datetime.datetime.now()
		try:
			# Expired verification requests
			psql.delete_rows_by_conditions(
				table="verification_requests",
				conditions={"expires_at": {"<": now}}
			)
			# Expired auth tokens
			psql.delete_rows_by_conditions(
				table="auth_tokens",
				conditions={"expires_at": {"<": now}}
			)
			logger.debug("Expired verification requests and auth tokens cleaned up.")
		except Exception as e:
			logger.exception("Error during cleanup of user data: %s", e)

	def get_user_by_uid(self, uid: str) -> Optional[Dict]:
		"""
		Retrieve user record by UID.
		"""
		result = psql.get_rows_by_conditions(
			table="users",
			conditions={"uid": uid}
		)
		return result[0] if result else None

	def get_user_by_auth_token(self, token: str) -> Optional[Dict]:
		"""
		Retrieve user record by auth token if it exists and is not expired.
		"""
		results = psql.get_rows_by_conditions(
			table="auth_tokens",
			conditions={"token": token}
		)
		if not results:
			return None

		row = results[0]
		if row["expires_at"] <= datetime.datetime.now():
			return None

		return self.get_user_by_uid(row["uid"])

	def get_user_by_email(self, email: str) -> Optional[Dict]:
		"""
		Retrieve user record by email.
		"""
		result = psql.get_rows_by_conditions(
			table="users",
			conditions={"email": email}
		)
		return result[0] if result else None

	@staticmethod
	def apply_hash(password: str) -> str:
		"""
		Hash a plaintext password using Argon2.
		"""
		return hasher.hash(password)

	def register_user(self, username: str, email: str, password: str) -> bool:
		"""
		Create a new user account, send verification email, and return True on success.
		"""
		if self.get_user_by_email(email):
			return False

		try:
			psql.insert_row(
				table="users",
				data={
					"uid": str(uuid.uuid4()),
					"username": username,
					"email": email,
					"password_hash": UserManagement.apply_hash(password),
				}
			)
			# Trigger sending of a verification email (this will insert a row into verification_requests)
			self.send_verification_email(email)
			logger.debug(f"User '{username}' registered and verification email queued.")
			return True
		except Exception as e:
			logger.exception("Error registering user '%s': %s", username, e)
			return False

	def send_verification_email(self, email: str) -> str:
		"""
		Send a verification email containing a one-time token link.
		"""
		user = self.get_user_by_email(email)
		if not user:
			return "User not found"
		if user.get("is_verified"):
			return "User already verified"

		expiry = datetime.datetime.now() + VERIFY_TOKEN_TTL

		# Generate a unique token, retrying on collision
		max_attempts = 3
		for attempt in range(max_attempts):
			verify_token = secrets.token_urlsafe(32)
			try:
				psql.insert_row(
					table="verification_requests",
					data={
						"verify_token": verify_token,
						"uid": user["uid"],
						"expires_at": expiry
					}
				)
				break
			except Exception as exc:
				# Ideally catch only unique-constraint exceptions from PSQLClient
				if attempt == max_attempts - 1:
					logger.exception("Failed to generate a unique verification token after %s attempts", max_attempts)
					raise RuntimeError("Could not generate unique verification token") from exc
				continue

		# Build external verification link using Flask's url_for
		try:
			verify_link = url_for("main.verify_email", token=verify_token, _external=True)
		except RuntimeError:
			# If called outside of an application context, fall back to a default URL
			domain = current_app.config.get("SERVER_NAME", "zubekanov.com")
			verify_link = f"https://{domain}/verify?token={verify_token}"

		# Load HTML email template
		email_template_path = ConfigReader.get_content_file("verification_email.html")
		with open(email_template_path, "r") as f:
			email_template = f.read()
		if not email_template:
			raise FileNotFoundError("Verification email template not found")
		icon_url = url_for("main.favicon", _external=True)
		email_content = email_template.format(
			icon_url=icon_url,
			username=user["username"],
			verify_link=verify_link,
		)

		# Load plaintext fallback
		plaintext_template_path = ConfigReader.get_content_file("verification_email.txt")
		with open(plaintext_template_path, "r") as f:
			plaintext_template = f.read()
		if not plaintext_template:
			raise FileNotFoundError("Verification email plaintext template not found")

		plaintext_content = plaintext_template.format(
			username=user["username"],
			verify_link=verify_link,
		)

		# Send via GmailClient
		gmail_client = GmailClient()
		response = gmail_client.send_html(
			to_addr=email,
			subject="Email Verification",
			plain=plaintext_content,
			html=email_content
		)
		if not response:
			logger.error("Failed to send verification email to %s", email)
			raise RuntimeError("Failed to send verification email")

		logger.debug("Verification email sent to %s with token %s", email, verify_token)
		return "Verification email sent successfully"

	def request_password_reset(self, email: str) -> None:
		user = self.get_user_by_email(email)
		if not user:
			logger.debug("Password reset requested for non-existent email: %s", email)
			return
		if not user.get("is_verified"):
			logger.debug("Password reset requested for unverified user: %s", email)
			return
		# Do not create duplicate requests
		existing_requests = psql.get_rows_by_conditions(
			table="password_reset_requests",
			conditions={"uid": user["uid"]}
		)
		# Doing a bad filter because raw_conditions is currently broken.
		existing_requests = [r for r in existing_requests if r["expires_at"] > datetime.datetime.now()]
		if existing_requests:
			logger.debug("Password reset already requested for user '%s'", user["username"])
			return
		
		# Generate a unique reset token
		reset_token = secrets.token_urlsafe(32)
		expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
		try:
			psql.insert_row(
				table="password_reset_requests",
				data={
					"reset_token": reset_token,
					"uid": user["uid"],
					"expires_at": expiry
				}
			)
			logger.debug("Password reset token generated for user '%s'", user["username"])
		except Exception as e:
			logger.exception("Failed to create password reset request for user '%s': %s", user["username"], e)
			raise RuntimeError("Failed to create password reset request") from e
		
		# Build reset link
		try:
			reset_link = url_for("main.reset_password", token=reset_token, _external=True)
		except RuntimeError:
			domain = current_app.config.get("SERVER_NAME", "zubekanov.com")
			reset_link = f"https://{domain}/reset-password?token={reset_token}"
		
		# Send email with reset link
		email_template_path = ConfigReader.get_content_file("password_reset_email.html")
		with open(email_template_path, "r") as f:
			email_template = f.read()
		if not email_template:
			raise FileNotFoundError("Password reset email template not found")
		icon_url = url_for("main.favicon", _external=True)
		email_content = email_template.format(
			icon_url=icon_url,
			username=user["username"],
			reset_link=reset_link,
		)
		plaintext_template_path = ConfigReader.get_content_file("password_reset_email.txt")
		with open(plaintext_template_path, "r") as f:
			plaintext_template = f.read()
		if not plaintext_template:
			raise FileNotFoundError("Password reset email plaintext template not found")
		plaintext_content = plaintext_template.format(
			username=user["username"],
			reset_link=reset_link,
		)
		gmail_client = GmailClient()
		response = gmail_client.send_html(
			to_addr=email,
			subject="Password Reset Request",
			plain=plaintext_content,
			html=email_content
		)
		if not response:
			logger.error("Failed to send password reset email to %s", email)
			raise RuntimeError("Failed to send password reset email")
		logger.debug("Password reset email sent to %s with token %s", email, reset_token)
		
	def reset_password(self, token: str, new_password: str) -> bool:
		"""
		Reset the user's password if the token exists and is not expired.
		"""
		results = psql.get_rows_by_conditions(
			table="password_reset_requests",
			conditions={"reset_token": token}
		)
		if not results:
			logger.debug("Invalid password reset token: %s", token)
			return False

		req = results[0]
		if req["expires_at"] <= datetime.datetime.now():
			logger.debug("Password reset token expired: %s", token)
			return False

		user = self.get_user_by_uid(req["uid"])
		if not user:
			logger.debug("No user found for UID: %s", req["uid"])
			return False

		try:
			psql.update_rows_by_conditions(
				table="users",
				updates={"password_hash": UserManagement.apply_hash(new_password)},
				conditions={"uid": user["uid"]}
			)
			psql.delete_rows_by_conditions(
				table="password_reset_requests",
				conditions={"reset_token": token}
			)
			logger.debug("Password reset successful for user '%s'", user["username"])
			return True
		except Exception as e:
			logger.exception("Failed to reset password for user '%s': %s", user["username"], e)
			return False

	def verify_user(self, token: str) -> bool:
		"""
		Mark a user as verified if token exists and is not expired.
		"""
		results = psql.get_rows_by_conditions(
			table="verification_requests",
			conditions={"verify_token": token}
		)
		if not results:
			return False

		req = results[0]
		if req["expires_at"] <= datetime.datetime.now():
			return False

		user = self.get_user_by_uid(req["uid"])
		if not user:
			return False

		# Update user record
		psql.update_rows_by_conditions(
			table="users",
			updates={"is_verified": True},
			conditions={"uid": user["uid"]}
		)
		logger.debug("User '%s' verified successfully", user["username"])
		return True

	def invalidate_auth_token(self, token: str) -> None:
		"""
		Delete an auth token row, effectively logging out that session.
		"""
		try:
			psql.delete_rows_by_conditions(
				table="auth_tokens",
				conditions={"token": token}
			)
			logger.debug("Auth token invalidated: %s", token)
		except Exception as e:
			logger.exception("Failed to invalidate auth token '%s': %s", token, e)

	def get_auth_token(self, email: str, password: str) -> Optional[str]:
		"""
		Validate email+password, create a new auth token row, and return the token string.
		"""
		user = self.get_user_by_email(email)
		if not user or not user.get("is_verified"):
			return None

		try:
			hasher.verify(user["password_hash"], password)
		except Exception:
			return None

		auth_token = secrets.token_urlsafe(32)
		expires = datetime.datetime.now() + datetime.timedelta(seconds=AUTH_TOKEN_TTL)
		try:
			psql.insert_row(
				table="auth_tokens",
				data={
					"token": auth_token,
					"uid": user["uid"],
					"expires_at": expires
				}
			)
			logger.debug("Generated new auth token for user '%s'", user["username"])
			return auth_token
		except Exception as e:
			logger.exception("Failed to store auth token for user '%s': %s", user["username"], e)
			return None

	def get_verification_auth_token(self, verification_token: str) -> Optional[str]:
		"""
		After a successful email verification, generate and return a new auth token.
		"""
		results = psql.get_rows_by_conditions(
			table="verification_requests",
			conditions={"verify_token": verification_token}
		)
		if not results:
			logger.debug("No verification request found for token: %s", verification_token)
			return None
		psql.delete_rows_by_conditions(
			table="verification_requests",
			conditions={"verify_token": verification_token}
		)

		uid = results[0]["uid"]
		user_row = self.get_user_by_uid(uid)
		if not user_row:
			logger.debug("No user found for UID: %s", uid)
			return None

		auth_token = secrets.token_urlsafe(32)
		expires = datetime.datetime.now() + datetime.timedelta(seconds=AUTH_TOKEN_TTL)
		try:
			psql.insert_row(
				table="auth_tokens",
				data={
					"token": auth_token,
					"uid": uid,
					"expires_at": expires
				}
			)
			logger.debug("Issued verification-based auth token for user '%s'", user_row["username"])
			return auth_token
		except Exception as e:
			logger.exception(
				"Failed to store verification-based auth token for user '%s': %s",
				user_row["username"], e
			)
			return None

	def get_user_by_auth_token(self, token: str) -> Optional[Dict]:
		"""
		If token exists and is not expired, return the corresponding user record.
		"""
		results = psql.get_rows_by_conditions(
			table="auth_tokens",
			conditions={"token": token}
		)
		if not results:
			return None

		row = results[0]
		if row["expires_at"] <= datetime.datetime.now():
			return None

		return self.get_user_by_uid(row["uid"])

	def _debug_wipe_users(self) -> None:
		"""
		Warning: Debug-only method to truncate users and all related data.
		"""
		try:
			psql.execute("TRUNCATE TABLE users RESTART IDENTITY CASCADE;")
			logger.debug("All user data wiped (debug).")
		except Exception as e:
			logger.exception("Failed to wipe user data: %s", e)
