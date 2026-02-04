import bcrypt
import glob
import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from sql.psql_client import PSQLClient
from util.fcr.file_config_reader import FileConfigReader
from util.auth_cache import session_cache

logger = logging.getLogger(__name__)
fcr = FileConfigReader()
config = fcr.find("website_db.conf")

class PSQLInterface:
	def __init__(self):
		self._client = PSQLClient(
			database=config.get("database"),
			user=config.get("user"),
			password=config.get("password"),
			host=config.get("host", None),
			port=config.get("port", None),
		)

	def _token_secret(self) -> bytes:
		secret_conf = fcr.find("secrets.conf")
		secret = secret_conf.get("WEBSITE_TOKEN_SECRET")
		if not secret:
			raise RuntimeError("Missing WEBSITE_TOKEN_SECRET.")
		return secret.encode("utf-8")

	def _generate_verification_token(self, nbytes: int = 32) -> str:
		return secrets.token_urlsafe(nbytes)

	def _hash_verification_token(self, token: str) -> str:
		"""
		Hash token for DB storage (HMAC-SHA256 hex).
		"""
		return hmac.new(
			self._token_secret(),
			token.encode("utf-8"),
			hashlib.sha256
		).hexdigest()

	def insert_pending_user(self, user_data: dict, force_insert: bool = False) -> tuple[bool, str]:
		"""
		Inserts a pending user into pending_users and returns:
			(True, raw_verification_token) on success
			(False, error_message) on failure

		Expects user_data to include: email, username, password
		Stores: password_hash, verification_token_hash, token_expires_at
		"""
		email = (user_data.get("email") or "").strip().lower()
		first_name = (user_data.get("first_name") or "").strip()
		last_name = (user_data.get("last_name") or "").strip()
		password = user_data.get("password")

		if force_insert:
			# Delete any existing pending user with the same email
			self._client.delete_rows_with_filters(
				"pending_users",
				equalities={"email": email},
			)

		invalid = any(
			pending_user.get("token_expires_at") and pending_user["token_expires_at"].replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
			for pending_user in self.get_pending_user({"email": email})
		)

		if invalid:
			return False, "A pending user with this email already exists."

		# Password hashing
		password_hash_bytes = bcrypt.hashpw(
			password.encode("utf-8"),
			bcrypt.gensalt(rounds=12)
		)
		password_hash = password_hash_bytes.decode("utf-8")

		# Verification token
		raw_token = self._generate_verification_token()
		token_hash = self._hash_verification_token(raw_token)

		# Expiry (UTC)
		expires_at = datetime.now(timezone.utc) + timedelta(hours=2)

		row = {
			"email": email,
			"first_name": first_name,
			"last_name": last_name,
			"password_hash": password_hash,
			"verification_token_hash": token_hash,
			"token_expires_at": expires_at,
		}

		try:
			self._client.insert_row("pending_users", row)
		except Exception as e:
			return False, f"Failed to create pending user: {e}"

		# Caller emails raw_token to the user.
		return True, raw_token
	
	def insert_user(self, user_data: dict) -> tuple[bool, str]:
		"""
		Inserts a new user into users table.
		"""
		id = user_data.get("id")
		email = user_data.get("email")
		first_name = user_data.get("first_name")
		last_name = user_data.get("last_name")
		password_hash = user_data.get("password_hash")

		existing_user = self.get_user({"email": email})
		if existing_user:
			return False, "A user with this email already exists."

		row = {
			"id": id,
			"email": email,
			"first_name": first_name,
			"last_name": last_name,
			"password_hash": password_hash,
		}

		try:
			self._client.insert_row("users", row)
		except Exception as e:
			return False, f"Failed to create user: {e}"

		return True, "User created successfully."
	
	def validate_verification_token(self, token: str) -> tuple[bool, str]:
		if not token or not str(token).strip():
			return False, "Missing verification token."
		token_hash = self._hash_verification_token(token.strip())

		try:
			pending_users = self.get_pending_user({"verification_token_hash": token_hash})
		except Exception as e:
			return False, f"Failed to read pending users: {e}"
		if not pending_users:
			return False, "Invalid verification token."

		now = datetime.now(timezone.utc)
		valid = [
			pending_user for pending_user in pending_users
			if pending_user.get("token_expires_at")
			and pending_user["token_expires_at"].replace(tzinfo=timezone.utc) > now
		]
		if not valid:
			return False, "Verification token expired."

		pending_user = valid[0]
		user_rows = self.get_user({"email": pending_user["email"]})
		if user_rows:
			user = user_rows[0]
			if user.get("is_active", True):
				if not user.get("is_anonymous"):
					return False, "Account already verified."
			try:
				self._client.update_rows_with_filters(
					"users",
					{
						"first_name": pending_user["first_name"],
						"last_name": pending_user["last_name"],
						"password_hash": pending_user["password_hash"],
						"is_anonymous": False,
						"is_active": True,
					},
					raw_conditions=["id = %s"],
					raw_params=[user["id"]],
				)
			except Exception as e:
				return False, f"Failed to restore account: {e}"
		else:
			status, message = self.insert_user({
				"id": pending_user["id"],
				"email": pending_user["email"],
				"first_name": pending_user["first_name"],
				"last_name": pending_user["last_name"],
				"password_hash": pending_user["password_hash"],
			})
			if not status:
				return False, message

		try:
			self._client.delete_rows_with_filters(
				"pending_users",
				raw_conditions=["id = %s"],
				raw_params=[pending_user["id"]],
			)
		except Exception:
			logger.exception("Failed to delete pending user after verification.")

		return True, "Email verified."
	
	def _generate_session_token(self, nbytes: int = 32) -> str:
		return secrets.token_urlsafe(nbytes)
	
	def _hash_session_token(self, token: str) -> str:
		return hmac.new(
			self._token_secret(),
			token.encode("utf-8"),
			hashlib.sha256
		).hexdigest()
	
	def login_user(self, email: str, password: str, remember_me: bool, ip: str, user_agent: str) -> tuple[bool, str]:
		# String is user token on success, error message on failure
		# Token is not activated from here yet
		users = self.get_user({"email": email.lower()})
		if len(users) == 0:
			logger.info("Login attempt failed: No user found with email '%s'", email.lower())
			return False, "Invalid email or password."
		user = users[0]
		if user.get("is_active") is False:
			logger.info("Login attempt failed: Inactive account for email '%s'", email.lower())
			return False, "Invalid email or password."
		stored_hash = user.get("password_hash")
		if not stored_hash:
			logger.info("Login attempt failed: No password hash stored for user with email '%s'", email.lower())
			return False, "Invalid email or password."
		if not bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")):
			logger.info("Login attempt failed: Incorrect password for user with email '%s'", email.lower())
			return False, "Invalid email or password."
		logger.info("Login attempt successful for user with email '%s'", email.lower())

		raw_token = self._generate_session_token()
		token_hash = self._hash_session_token(raw_token)
		ttl_days = 30 if remember_me else 1
		now = datetime.now(timezone.utc)

		if len(ip) > 45:
			ip = ip[:42] + "..."
		if len(user_agent) > 255:
			user_agent = user_agent[:252] + "..."

		try:
			# If there is an existing active session for the same user/ip/user_agent,
			# rotate the token hash in-place and reuse that session slot.
			existing, _ = self._client.get_rows_with_filters(
				"user_sessions",
				raw_conditions=[
					"user_id = %s",
					"ip = %s",
					"user_agent = %s",
					"expires_at >= NOW()",
					"revoked_at IS NULL",
				],
				raw_params=[user["id"], ip, user_agent],
				page_limit=1,
				page_num=0,
				order_by="last_seen_at",
				order_dir="DESC",
			)

			if existing:
				self._client.update_rows_with_filters(
					"user_sessions",
					{
						"session_token_hash": token_hash,
						"last_seen_at": now,
						"expires_at": now + timedelta(days=ttl_days),
						"revoked_at": None,
					},
					raw_conditions=["id = %s"],
					raw_params=[existing[0]["id"]],
				)
				# Remove any additional active sessions for the same tuple.
				self._client.delete_rows_with_filters(
					"user_sessions",
					raw_conditions=[
						"user_id = %s",
						"ip = %s",
						"user_agent = %s",
						"session_token_hash <> %s",
					],
					raw_params=[user["id"], ip, user_agent, token_hash],
				)
			else:
				row = {
					"user_id": user["id"],
					"session_token_hash": token_hash,
					"ip": ip,
					"user_agent": user_agent,
					"created_at": now,
					"last_seen_at": now,
					"expires_at": now + timedelta(days=ttl_days),
					"revoked_at": None,
				}
				self._client.insert_row("user_sessions", row)

			self._client.update_rows_with_equalities(
				"users",
				{"last_login_at": now},
				{"id": user["id"]},
			)
		except Exception as e:
			return False, f"Login failed: {e}"
		
		return True, raw_token

	def update_user_password(self, user_id: str, new_password: str) -> tuple[bool, str]:
		if not user_id:
			return False, "user_id is required."
		if not new_password or len(new_password) < 8:
			return False, "Password must be at least 8 characters long."

		try:
			password_hash_bytes = bcrypt.hashpw(
				new_password.encode("utf-8"),
				bcrypt.gensalt(rounds=12)
			)
			password_hash = password_hash_bytes.decode("utf-8")
			self._client.update_rows_with_equalities(
				"users",
				{"password_hash": password_hash, "is_anonymous": False},
				{"id": user_id},
			)
			return True, "Password updated."
		except Exception as e:
			return False, f"Failed to update password: {e}"
	
	def check_session_token(self, raw_token: str) -> dict | None:
		if not raw_token:
			return None

		token_hash = self._hash_session_token(raw_token)

		# Cache lookup
		cached = session_cache.get(token_hash)
		if cached is not None:
			logging.info("Session token cache hit.")
			if cached:
				try:
					exists = self.get_user({"id": cached.get("id")})
				except Exception:
					exists = []
				if not exists:
					session_cache.delete(token_hash)
					return None
			return cached or None

		# DB lookup
		rows, pages = self._client.get_rows_with_filters(
			"user_sessions",
			raw_conditions=[
				"session_token_hash = %s",
				"expires_at >= NOW()",
				"revoked_at IS NULL",
			],
			raw_params=[token_hash],
			page_limit=1,
			page_num=0,
		)

		if not rows:
			session_cache.set(token_hash, None, ttl_seconds=30)
			return None

		sess = rows[0]
		self._cleanup_user_sessions(sess["user_id"])
		user_rows = self.get_user({"id": sess["user_id"]})
		if not user_rows:
			session_cache.set(token_hash, None, ttl_seconds=30)
			return None
		user = user_rows[0]
		exp = sess["expires_at"]
		if exp.tzinfo is None:
			exp = exp.replace(tzinfo=timezone.utc)
		ttl = int((exp - datetime.now(timezone.utc)).total_seconds())
		ttl = max(1, min(ttl, 300))

		session_cache.set(token_hash, user, ttl_seconds=ttl)
		return user

	def _cleanup_user_sessions(self, user_id: str) -> None:
		if not user_id:
			return
		try:
			# Remove expired sessions for this user.
			self._client.delete_rows_with_filters(
				"user_sessions",
				raw_conditions=[
					"user_id = %s",
					"expires_at < NOW()",
				],
				raw_params=[user_id],
			)

			# Enforce single active session per (user_id, ip, user_agent).
			self._client.delete_rows_with_filters(
				"user_sessions",
				raw_conditions=[
					"user_id = %s",
					"ctid IN (SELECT ctid FROM ("
					"SELECT ctid, ROW_NUMBER() OVER (PARTITION BY user_id, ip, user_agent "
					"ORDER BY last_seen_at DESC NULLS LAST, created_at DESC NULLS LAST) AS rn "
					"FROM user_sessions WHERE user_id = %s AND revoked_at IS NULL AND expires_at >= NOW()"
					") s WHERE s.rn > 1)",
				],
				raw_params=[user_id, user_id],
			)
		except Exception:
			logger.exception("Failed to cleanup user sessions for user_id=%s", user_id)
	
	# Just calls the psqlclient function but is a bit more streamlined and checks columns
	def get_user(self, match_fields: dict):
		if not hasattr(self, "user_columns"):
			self.user_columns = self._client.get_column_info("public", "users").keys()
		invalid_keys = [key for key in match_fields if key not in self.user_columns]
		if invalid_keys:
			raise ValueError(f"Invalid keys in match_fields: {invalid_keys}")
		return self._client.get_rows_with_filters(
			"users",
			equalities=match_fields,
		)[0]
	
	def get_pending_user(self, match_fields: dict):
		if not hasattr(self, "pending_user_columns"):
			self.pending_user_columns = self._client.get_column_info("public", "pending_users").keys()
		invalid_keys = [key for key in match_fields if key not in self.pending_user_columns]
		if invalid_keys:
			raise ValueError(f"Invalid keys in match_fields: {invalid_keys}")
		return self._client.get_rows_with_filters(
			"pending_users",
			equalities=match_fields,
		)[0]
	
	def get_pending_or_verified_user(self, match_fields: dict):
		user = self.get_user(match_fields)
		if user:
			return user
		return self.get_pending_user(match_fields)

	def is_admin(self, user_id: str) -> bool:
		if not user_id:
			return False
		rows, _ = self._client.get_rows_with_filters(
			"admins",
			equalities={"user_id": user_id},
			page_limit=1,
			page_num=0,
		)
		return bool(rows)

	def promote_user_to_admin(self, user_id: str, note: str | None = None) -> tuple[bool, str]:
		if not user_id:
			return False, "user_id is required."
		if self.is_admin(user_id):
			return True, "User is already an admin."

		row = {
			"user_id": user_id,
			"note": note,
			"created_at": datetime.now(timezone.utc),
		}
		try:
			self._client.insert_row("admins", row)
		except Exception as e:
			return False, f"Failed to promote user: {e}"
		return True, "User promoted to admin."

	def demote_user_from_admin(self, user_id: str) -> tuple[bool, str]:
		if not user_id:
			return False, "user_id is required."
		try:
			deleted = self._client.delete_rows_with_filters(
				"admins",
				equalities={"user_id": user_id},
			)
		except Exception as e:
			return False, f"Failed to demote user: {e}"
		if deleted == 0:
			return False, "User is not an admin."
		return True, "User demoted from admin."
 
	@property
	def client(self):
		return self._client

	def execute_query(self, query, params=None):
		return self._client.execute_query(query, params)

	def get_user_by_email_case_insensitive(self, email: str):
		try:
			return self.execute_query(
				"SELECT id, first_name, last_name, is_anonymous FROM users WHERE LOWER(email) = LOWER(%s) LIMIT 1;",
				(email,),
			) or []
		except Exception:
			return self.execute_query(
				"SELECT id, first_name, last_name FROM users WHERE LOWER(email) = LOWER(%s) LIMIT 1;",
				(email,),
			) or []

	def get_discord_subscription_for_user(self, subscription_id: str, user_id: str):
		return self.execute_query(
			"SELECT s.id FROM discord_webhook_subscriptions s "
			"JOIN discord_webhooks w ON w.id = s.webhook_id "
			"WHERE s.id = %s AND w.user_id = %s LIMIT 1;",
			(subscription_id, user_id),
		) or []

	def get_discord_subscription_with_webhook_active(self, subscription_id: str, user_id: str):
		return self.execute_query(
			"SELECT s.id, w.is_active AS webhook_active "
			"FROM discord_webhook_subscriptions s "
			"JOIN discord_webhooks w ON w.id = s.webhook_id "
			"WHERE s.id = %s AND w.user_id = %s LIMIT 1;",
			(subscription_id, user_id),
		) or []

	def get_discord_webhook_for_user(self, webhook_id: str, user_id: str):
		return self.execute_query(
			"SELECT id FROM discord_webhooks WHERE id = %s AND user_id = %s LIMIT 1;",
			(webhook_id, user_id),
		) or []

	def get_discord_webhook_by_url(self, webhook_url: str):
		return self.execute_query(
			"SELECT id, is_active FROM discord_webhooks WHERE webhook_url = %s LIMIT 1;",
			(webhook_url,),
		) or []

	def get_discord_webhook_id_by_url_and_user(self, webhook_url: str, user_id: str):
		return self.execute_query(
			"SELECT id FROM discord_webhooks WHERE webhook_url = %s AND user_id = %s LIMIT 1;",
			(webhook_url, user_id),
		) or []

	def get_discord_subscription_by_webhook_url_event_key(self, webhook_url: str, event_key: str):
		return self.execute_query(
			"SELECT s.id, s.is_active, w.is_active AS webhook_active "
			"FROM discord_webhook_subscriptions s "
			"JOIN discord_webhooks w ON w.id = s.webhook_id "
			"WHERE w.webhook_url = %s AND s.event_key = %s LIMIT 1;",
			(webhook_url, event_key),
		) or []

	def get_discord_webhook_registration_by_url_event_key(self, webhook_url: str, event_key: str):
		return self.execute_query(
			"SELECT 1 FROM discord_webhook_registrations WHERE webhook_url = %s AND event_key = %s LIMIT 1;",
			(webhook_url, event_key),
		) or []

	def get_discord_webhook_registration_basic_by_id(self, reg_id: str):
		return self.execute_query(
			"SELECT name, event_key, webhook_url FROM discord_webhook_registrations WHERE id = %s;",
			(reg_id,),
		) or []

	def get_application_exemption(self, user_id: str, integration_type: str):
		return self.execute_query(
			"SELECT id FROM application_exemptions "
			"WHERE user_id = %s AND integration_type = %s LIMIT 1;",
			(user_id, integration_type),
		) or []

	def get_application_exemption_with_key(self, user_id: str, integration_type: str, integration_key: str):
		return self.execute_query(
			"SELECT id FROM application_exemptions "
			"WHERE user_id = %s AND integration_type = %s AND integration_key = %s LIMIT 1;",
			(user_id, integration_type, integration_key),
		) or []

	def get_minecraft_registration_by_id(self, reg_id: str):
		return self.execute_query(
			"SELECT * FROM minecraft_registrations WHERE id = %s;",
			(reg_id,),
		) or []

	def get_minecraft_registration_by_username(self, mc_username: str):
		return self.execute_query(
			"SELECT 1 FROM minecraft_registrations WHERE LOWER(mc_username) = LOWER(%s) LIMIT 1;",
			(mc_username,),
		) or []

	def get_minecraft_whitelist_by_username(self, mc_username: str):
		return self.execute_query(
			"SELECT id, is_active FROM minecraft_whitelist WHERE LOWER(mc_username) = LOWER(%s) LIMIT 1;",
			(mc_username,),
		) or []

	def get_minecraft_whitelist_active_by_username(self, mc_username: str):
		return self.execute_query(
			"SELECT 1 FROM minecraft_whitelist WHERE LOWER(mc_username) = LOWER(%s) AND is_active = TRUE LIMIT 1;",
			(mc_username,),
		) or []

	def get_minecraft_whitelist_by_user_and_username(self, user_id: str, mc_username: str):
		return self.execute_query(
			"SELECT id FROM minecraft_whitelist WHERE user_id = %s AND LOWER(mc_username) = LOWER(%s) LIMIT 1;",
			(user_id, mc_username),
		) or []

	def get_minecraft_whitelist_entry_for_user(self, whitelist_id: str, user_id: str):
		return self.execute_query(
			"SELECT id, ban_reason FROM minecraft_whitelist WHERE id = %s AND user_id = %s LIMIT 1;",
			(whitelist_id, user_id),
		) or []

	def get_minecraft_whitelist_username_by_id(self, whitelist_id: str):
		return self.execute_query(
			"SELECT mc_username FROM minecraft_whitelist WHERE id = %s LIMIT 1;",
			(whitelist_id,),
		) or []

	def get_audiobookshelf_registration_for_user(self, reg_id: str, user_id: str):
		return self.execute_query(
			"SELECT id FROM audiobookshelf_registrations WHERE id = %s AND user_id = %s LIMIT 1;",
			(reg_id, user_id),
		) or []

	def get_audiobookshelf_registration_contact_by_id(self, reg_id: str):
		return self.execute_query(
			"SELECT first_name, last_name, email, user_id FROM audiobookshelf_registrations WHERE id = %s;",
			(reg_id,),
		) or []

	def get_discord_webhook_registration_contact_by_id(self, reg_id: str):
		return self.execute_query(
			"SELECT name, event_key, webhook_url, submitted_by_user_id, submitted_by_email "
			"FROM discord_webhook_registrations WHERE id = %s;",
			(reg_id,),
		) or []

	def get_discord_webhook_subscriptions(self, webhook_id):
		return self.execute_query(
			"SELECT s.id, s.event_key, s.is_active, s.created_at, "
			"ek.permission, ek.description "
			"FROM discord_webhook_subscriptions s "
			"LEFT JOIN discord_event_keys ek ON ek.event_key = s.event_key "
			"WHERE s.webhook_id = %s "
			"ORDER BY "
			"CASE COALESCE(ek.permission, '') "
			"WHEN 'admins' THEN 1 "
			"WHEN 'users' THEN 2 "
			"WHEN 'all' THEN 3 "
			"ELSE 4 END, "
			"s.created_at DESC;",
			(webhook_id,),
		) or []

	def get_active_minecraft_whitelist_usernames(self, user_id):
		return self.execute_query(
			"SELECT mc_username FROM minecraft_whitelist WHERE user_id = %s AND is_active = TRUE;",
			(user_id,),
		) or []

	def count_pending_audiobookshelf_registrations(self) -> int | None:
		return self._count_pending_status("audiobookshelf_registrations")

	def count_pending_discord_webhook_registrations(self) -> int | None:
		return self._count_pending_status("discord_webhook_registrations")

	def count_pending_minecraft_registrations(self) -> int | None:
		return self._count_pending_status("minecraft_registrations")

	def _count_pending_status(self, table: str) -> int | None:
		try:
			rows = self.execute_query(
				f"SELECT COUNT(*) AS cnt FROM {table} WHERE status = %s;",
				("pending",),
			) or []
			return int(rows[0]["cnt"]) if rows else 0
		except Exception:
			return None

	def get_admin_user_management_rows(self, limit: int = 200):
		return self.execute_query(
			"SELECT id, first_name, last_name, email, created_at, is_active, is_anonymous "
			"FROM users u "
			"WHERE COALESCE(u.is_active, TRUE) = TRUE "
			"AND ("
			"COALESCE(u.is_anonymous, FALSE) = FALSE "
			"OR EXISTS (SELECT 1 FROM discord_webhooks w WHERE w.user_id = u.id AND COALESCE(w.is_active, TRUE) = TRUE) "
			"OR EXISTS (SELECT 1 FROM minecraft_whitelist m WHERE m.user_id = u.id AND COALESCE(m.is_active, TRUE) = TRUE) "
			"OR EXISTS (SELECT 1 FROM audiobookshelf_registrations a WHERE a.user_id = u.id "
			"AND a.status = 'approved' AND COALESCE(a.is_active, TRUE) = TRUE)"
			") "
			"ORDER BY created_at DESC LIMIT %s;",
			(limit,),
		) or []
	
	def verify_tables(self, safe_mode: bool = True) -> None:
		tables_dir = os.path.join(os.path.dirname(__file__), "tables")
		json_files = glob.glob(os.path.join(tables_dir, "*.json"))
		configured: set[tuple[str, str]] = set()

		for json_file in json_files:
			table_config_name = os.path.basename(json_file)
			self.verify_table(table_config_name, safe_mode=safe_mode)
			try:
				table_config = fcr.find(table_config_name)
				tables = self._normalise_tables_config(table_config)
				for t in tables:
					schema = t.get("schema", "public")
					name = t.get("table_name")
					if name:
						configured.add((schema, name))
			except Exception:
				logger.exception("Failed to load table config for %s", table_config_name)

		# Migrate legacy anonymous_users into users before dropping unknown tables.
		self._migrate_anonymous_users_to_users()

		# Drop tables not present in configs (only within configured schemas).
		schemas = {schema for schema, _ in configured}
		for schema in schemas:
			try:
				existing = set(self.client.list_tables(schema))
			except Exception:
				logger.exception("Failed to list tables for schema %s", schema)
				continue
			allowed = {name for s, name in configured if s == schema}
			for table in sorted(existing - allowed):
				try:
					self.client.drop_table(schema, table, cascade=True, missing_ok=True)
					logger.warning("Dropped table not in config: %s.%s", schema, table)
				except Exception:
					logger.exception("Failed to drop table %s.%s", schema, table)

	def _migrate_anonymous_users_to_users(self) -> None:
		"""
		Migrate legacy anonymous_users rows into users with is_anonymous=true.
		"""
		try:
			if not self.client.table_exists("public", "anonymous_users"):
				return
			if not self.client.table_exists("public", "users"):
				return
		except Exception:
			logger.exception("Failed to check table existence for anonymous_users migration")
			return

		try:
			rows = self.execute_query(
				'SELECT id, first_name, last_name, email, created_at FROM "public"."anonymous_users";'
			) or []
		except Exception:
			logger.exception("Failed to read anonymous_users rows for migration")
			return

		for row in rows:
			email = (row.get("email") or "").strip().lower()
			if not email:
				continue
			try:
				existing = self.execute_query(
					'SELECT id, is_anonymous FROM "public"."users" WHERE LOWER(email) = LOWER(%s) LIMIT 1;',
					(email,),
				) or []
				if existing:
					continue
				self.client.insert_row("users", {
					"id": row.get("id"),
					"email": email,
					"first_name": row.get("first_name") or "",
					"last_name": row.get("last_name") or "",
					"password_hash": None,
					"is_active": True,
					"is_anonymous": True,
					"created_at": row.get("created_at"),
				})
				logger.info("Migrated anonymous user %s into users", email)
			except Exception:
				logger.exception("Failed to migrate anonymous user %s", email)

	def verify_table(self, table_config_name: str, *, safe_mode: bool = True) -> None:
		"""
		safe_mode=True:
			- only additive changes (create table, add columns, add indexes, add constraints where possible)
			- does NOT drop columns, does NOT change types, does NOT change nullability/defaults
		"""
		table_config = fcr.find(table_config_name)
		logger.debug("Verifying table(s) from config: %s", table_config_name)
		tables = self._normalise_tables_config(table_config)

		for t in tables:
			self._verify_one_table(t, safe_mode=safe_mode)

	def _normalise_tables_config(self, cfg) -> list[dict]:
		if cfg is None:
			raise ValueError("No table config loaded")

		if isinstance(cfg, list):
			return cfg

		if isinstance(cfg, dict) and "tables" in cfg and isinstance(cfg["tables"], list):
			return cfg["tables"]

		if isinstance(cfg, dict) and "table_name" in cfg:
			return [cfg]

		raise ValueError("Unsupported table config structure")

	def _verify_one_table(self, t: dict, *, safe_mode: bool) -> None:
		schema = t.get("schema", "public")
		table = t["table_name"]
		columns_cfg = t.get("columns", [])
		indexes_cfg = t.get("indexes", [])

		self.client.ensure_schema(schema)

		if not self.client.table_exists(schema, table):
			self._create_table_from_config(schema, table, columns_cfg, indexes_cfg)
			logger.info("Created table %s.%s", schema, table)
			return

		if safe_mode:
			has_changes = self._alter_table_additive(schema, table, columns_cfg, indexes_cfg, safe_mode=safe_mode)
		else:
			has_changes = self._alter_table_forceful(schema, table, columns_cfg, indexes_cfg)
		if not has_changes:
			logger.debug("Verified table %s.%s (no changes)", schema, table)
		else:
			logger.info("Verified table %s.%s (changes applied)", schema, table)
		
	def _create_table_from_config(self, schema: str, table: str, columns_cfg: list[dict], indexes_cfg: list[dict]) -> None:
		columns = {}
		constraints = []

		for c in columns_cfg:
			col_name = c["name"]
			col_sql = self._column_type_sql(c)

			mod_bits = []
			if not c.get("nullable", True):
				mod_bits.append("NOT NULL")
			if "default" in c and c["default"] is not None:
				mod_bits.append(f"DEFAULT {c['default']}")

			if c.get("primary_key", False):
				mod_bits.append("PRIMARY KEY")

			columns[col_name] = (col_sql + (" " + " ".join(mod_bits) if mod_bits else "")).strip()

		# Table-level unique constraints
		for c in columns_cfg:
			if c.get("unique", False) and not c.get("primary_key", False):
				con_name = f"{table}_{c['name']}_key"
				constraints.append(f'CONSTRAINT "{con_name}" UNIQUE ("{c["name"]}")')

		# Foreign keys
		for c in columns_cfg:
			fk = c.get("foreign_key")
			if fk:
				con_name = f"{table}_{c['name']}_fkey"
				on_delete = fk.get("on_delete")
				on_update = fk.get("on_update")
				frag = f'CONSTRAINT "{con_name}" FOREIGN KEY ("{c["name"]}") REFERENCES "{fk["table"]}" ("{fk["column"]}")'
				if on_delete:
					frag += f" ON DELETE {on_delete.upper()}"
				if on_update:
					frag += f" ON UPDATE {on_update.upper()}"
				constraints.append(frag)
			enum_vals = c.get("enum")
			if enum_vals:
				con_name = f"{table}_{c['name']}_enum_check"
				enum_sql = self._enum_values_sql(enum_vals)
				constraints.append(f'CONSTRAINT "{con_name}" CHECK ("{c["name"]}" IN ({enum_sql}))')

		self.client.create_table(schema, table, columns, constraints=constraints, if_not_exists=True)

		# Indexes
		self._ensure_indexes(schema, table, indexes_cfg, columns_cfg)
		logger.info("Created table %s.%s", schema, table)

	def _alter_table_additive(self, schema: str, table: str, columns_cfg: list[dict], indexes_cfg: list[dict], *, safe_mode: bool) -> bool:
		existing_cols = self.client.get_column_info(schema, table)
		existing_colnames = set(existing_cols.keys())
		changes_detected = False

		for c in columns_cfg:
			name = c["name"]
			if name in existing_colnames:
				continue

			type_sql = self._column_type_sql(c)

			mod_bits = []
			if "default" in c and c["default"] is not None:
				mod_bits.append(f"DEFAULT {c['default']}")

			if not c.get("nullable", True):
				if safe_mode and "default" not in c:
					logger.warning(
						"Adding column %s.%s.%s as NULLABLE (safe_mode). Config wants NOT NULL but no default was provided.",
						schema, table, name
					)
				else:
					mod_bits.append("NOT NULL")

			col_def = (type_sql + (" " + " ".join(mod_bits) if mod_bits else "")).strip()
			self.client.add_column(schema, table, name, col_def)
			logger.info("Added column %s.%s.%s", schema, table, name)
			changes_detected = True

		for c in columns_cfg:
			if c.get("unique", False) and not c.get("primary_key", False):
				con_name = f"{table}_{c['name']}_key"
				if not self.client.constraint_exists(schema, table, con_name):
					self.client.add_constraint(schema, table, f'CONSTRAINT "{con_name}" UNIQUE ("{c["name"]}")')
					logger.info("Added UNIQUE constraint %s on %s.%s(%s)", con_name, schema, table, c["name"])
					changes_detected = True

			fk = c.get("foreign_key")
			if fk:
				con_name = f"{table}_{c['name']}_fkey"
				if not self.client.constraint_exists(schema, table, con_name):
					on_delete = fk.get("on_delete")
					on_update = fk.get("on_update")
					frag = f'CONSTRAINT "{con_name}" FOREIGN KEY ("{c["name"]}") REFERENCES "{fk["table"]}" ("{fk["column"]}")'
					if on_delete:
						frag += f" ON DELETE {on_delete.upper()}"
					if on_update:
						frag += f" ON UPDATE {on_update.upper()}"
					self.client.add_constraint(schema, table, frag)
					logger.info("Added FK constraint %s on %s.%s(%s)", con_name, schema, table, c["name"])
					changes_detected = True
			enum_vals = c.get("enum")
			if enum_vals:
				con_name = f"{table}_{c['name']}_enum_check"
				if not self.client.constraint_exists(schema, table, con_name):
					enum_sql = self._enum_values_sql(enum_vals)
					self.client.add_constraint(schema, table, f'CONSTRAINT "{con_name}" CHECK ("{c["name"]}" IN ({enum_sql}))')
					logger.info("Added CHECK constraint %s on %s.%s(%s)", con_name, schema, table, c["name"])
					changes_detected = True

		if self._ensure_indexes(schema, table, indexes_cfg, columns_cfg):
			changes_detected = True

		config_cols = {c["name"] for c in columns_cfg}
		extras = sorted(existing_colnames - config_cols)
		if extras:
			logger.warning("Table %s.%s has extra columns not in config: %s", schema, table, extras)

		return changes_detected

	def _ensure_indexes(self, schema: str, table: str, indexes_cfg: list[dict], columns_cfg: list[dict]) -> bool:
		existing = {r["indexname"] for r in self.client.list_indexes(schema, table)}
		changed = False

		for c in columns_cfg:
			if c.get("index", False):
				idx_name = f"{table}_{c['name']}_idx"
				if idx_name not in existing:
					self.client.create_index(schema, table, idx_name, [c["name"]], unique=False, if_not_exists=True)
					logger.info("Created index %s on %s.%s(%s)", idx_name, schema, table, c["name"])
					changed = True

		for idx in indexes_cfg:
			name = idx["name"]
			if name in existing:
				continue
			cols = idx["columns"]
			unique = bool(idx.get("unique", False))
			self.client.create_index(schema, table, name, cols, unique=unique, if_not_exists=True)
			logger.info("Created index %s on %s.%s(%s)", name, schema, table, ", ".join(cols))
			changed = True

		return changed

	def _alter_table_forceful(self, schema: str, table: str, columns_cfg: list[dict], indexes_cfg: list[dict]) -> bool:
		changed = False
		existing_cols = self.client.get_column_info(schema, table)
		existing_colnames = set(existing_cols.keys())
		config_cols = {c["name"] for c in columns_cfg}
		not_null_targets: list[str] = []

		# Add missing columns with full definition
		for c in columns_cfg:
			name = c["name"]
			if name in existing_colnames:
				continue
			type_sql = self._column_type_sql(c)
			mod_bits = []
			if not c.get("nullable", True):
				# Avoid immediate NOT NULL on existing rows; enforce after cleanup.
				not_null_targets.append(name)
			if "default" in c and c["default"] is not None:
				mod_bits.append(f"DEFAULT {self._default_sql(c['default'])}")
			if c.get("primary_key", False):
				mod_bits.append("PRIMARY KEY")
			col_def = (type_sql + (" " + " ".join(mod_bits) if mod_bits else "")).strip()
			self.client.add_column(schema, table, name, col_def)
			logger.info("Added column %s.%s.%s", schema, table, name)
			changed = True

		# Alter existing columns to match type/nullability/default
		for c in columns_cfg:
			name = c["name"]
			if name not in existing_cols:
				continue
			info = existing_cols[name]
			expected_sig = self._expected_type_signature(c)
			existing_sig = self._existing_type_signature(info)
			if expected_sig != existing_sig:
				type_sql = self._column_type_sql(c)
				self.client.alter_column_type(schema, table, name, type_sql)
				logger.info("Altered type of %s.%s.%s to %s", schema, table, name, type_sql)
				changed = True

			nullable_expected = bool(c.get("nullable", True))
			nullable_current = (str(info.get("is_nullable", "YES")).upper() == "YES")
			if nullable_expected != nullable_current:
				if not nullable_expected:
					self._cleanup_nulls(schema, table, name)
				self.client.alter_column_nullability(schema, table, name, nullable=nullable_expected)
				logger.info("Altered nullability of %s.%s.%s to %s", schema, table, name, "NULL" if nullable_expected else "NOT NULL")
				changed = True

			has_default = ("default" in c and c["default"] is not None)
			current_default = info.get("column_default")
			if has_default:
				expected_default = self._normalize_default(self._default_sql(c["default"]))
				current_norm = self._normalize_default(current_default)
				if expected_default != current_norm:
					self.client.alter_column_default(schema, table, name, default_sql=self._default_sql(c["default"]))
					logger.info("Altered default of %s.%s.%s", schema, table, name)
					changed = True
			else:
				if current_default is not None:
					self.client.alter_column_default(schema, table, name, drop=True)
					logger.info("Dropped default of %s.%s.%s", schema, table, name)
					changed = True

		# Enforce NOT NULL for newly added columns after cleaning rows.
		for name in not_null_targets:
			try:
				self._cleanup_nulls(schema, table, name)
				self.client.alter_column_nullability(schema, table, name, nullable=False)
				logger.info("Enforced NOT NULL on %s.%s.%s after cleanup", schema, table, name)
				changed = True
			except Exception:
				logger.exception("Failed to enforce NOT NULL on %s.%s.%s", schema, table, name)

		# Drop extra columns
		extras = sorted(existing_colnames - config_cols)
		for col in extras:
			self.client.drop_column(schema, table, col, cascade=True, missing_ok=True)
			logger.info("Dropped extra column %s.%s.%s", schema, table, col)
			changed = True

		# Constraints (PK/UNIQUE/FK)
		expected_constraints = set()
		pk_columns = [c["name"] for c in columns_cfg if c.get("primary_key", False)]
		if pk_columns:
			expected_constraints.add(f"{table}_pkey")

		for c in columns_cfg:
			if c.get("unique", False) and not c.get("primary_key", False):
				expected_constraints.add(f"{table}_{c['name']}_key")
			if c.get("foreign_key"):
				expected_constraints.add(f"{table}_{c['name']}_fkey")
			if c.get("enum"):
				expected_constraints.add(f"{table}_{c['name']}_enum_check")

		existing_constraints = self.client.list_constraints(schema, table)
		for con in existing_constraints:
			if con["constraint_type"] not in {"PRIMARY KEY", "UNIQUE", "FOREIGN KEY", "CHECK"}:
				continue
			con_name = con["constraint_name"]
			if con_name.endswith("_not_null"):
				continue
			if con_name not in expected_constraints:
				self.client.drop_constraint(schema, table, con_name, missing_ok=True)
				logger.info("Dropped constraint %s on %s.%s", con_name, schema, table)
				changed = True

		# Ensure primary key
		if pk_columns:
			pk_name = f"{table}_pkey"
			if not self.client.constraint_exists(schema, table, pk_name):
				col_list = ", ".join(f'"{c}"' for c in pk_columns)
				self.client.add_constraint(schema, table, f'CONSTRAINT "{pk_name}" PRIMARY KEY ({col_list})')
				logger.info("Added PRIMARY KEY %s on %s.%s(%s)", pk_name, schema, table, ", ".join(pk_columns))
				changed = True
			else:
				current_pk_cols = self.client.get_constraint_columns(schema, table, pk_name)
				if set(current_pk_cols) != set(pk_columns):
					self.client.drop_constraint(schema, table, pk_name, missing_ok=True)
					col_list = ", ".join(f'"{c}"' for c in pk_columns)
					self.client.add_constraint(schema, table, f'CONSTRAINT "{pk_name}" PRIMARY KEY ({col_list})')
					logger.info("Rebuilt PRIMARY KEY %s on %s.%s(%s)", pk_name, schema, table, ", ".join(pk_columns))
					changed = True

		# Ensure unique and foreign key constraints
		for c in columns_cfg:
			if c.get("unique", False) and not c.get("primary_key", False):
				con_name = f"{table}_{c['name']}_key"
				if not self.client.constraint_exists(schema, table, con_name):
					self.client.add_constraint(schema, table, f'CONSTRAINT "{con_name}" UNIQUE ("{c["name"]}")')
					logger.info("Added UNIQUE constraint %s on %s.%s(%s)", con_name, schema, table, c["name"])
					changed = True

			fk = c.get("foreign_key")
			if fk:
				con_name = f"{table}_{c['name']}_fkey"
				if not self.client.constraint_exists(schema, table, con_name):
					on_delete = fk.get("on_delete")
					on_update = fk.get("on_update")
					frag = f'CONSTRAINT "{con_name}" FOREIGN KEY ("{c["name"]}") REFERENCES "{fk["table"]}" ("{fk["column"]}")'
					if on_delete:
						frag += f" ON DELETE {on_delete.upper()}"
					if on_update:
						frag += f" ON UPDATE {on_update.upper()}"
					self.client.add_constraint(schema, table, frag)
					logger.info("Added FK constraint %s on %s.%s(%s)", con_name, schema, table, c["name"])
					changed = True
			enum_vals = c.get("enum")
			if enum_vals:
				con_name = f"{table}_{c['name']}_enum_check"
				if not self.client.constraint_exists(schema, table, con_name):
					enum_sql = self._enum_values_sql(enum_vals)
					self.client.add_constraint(schema, table, f'CONSTRAINT "{con_name}" CHECK ("{c["name"]}" IN ({enum_sql}))')
					logger.info("Added CHECK constraint %s on %s.%s(%s)", con_name, schema, table, c["name"])
					changed = True

		# Indexes
		if self._ensure_indexes(schema, table, indexes_cfg, columns_cfg):
			changed = True

		expected_indexes = set()
		for c in columns_cfg:
			if c.get("index", False):
				expected_indexes.add(f"{table}_{c['name']}_idx")
		for idx in indexes_cfg:
			expected_indexes.add(idx["name"])

		existing_indexes = {r["indexname"] for r in self.client.list_indexes(schema, table)}
		constraint_indexes = set(self.client.list_constraint_indexes(schema, table))
		for idx_name in sorted(existing_indexes - expected_indexes - constraint_indexes):
			self.client.drop_index(schema, idx_name, missing_ok=True)
			logger.info("Dropped index %s on %s.%s", idx_name, schema, table)
			changed = True

		return changed

	def _default_sql(self, value) -> str:
		if isinstance(value, bool):
			return "TRUE" if value else "FALSE"
		if value is None:
			return "NULL"
		return str(value)

	def _normalize_default(self, value) -> str | None:
		if value is None:
			return None
		s = str(value).strip()
		if not s:
			return None
		# Strip PostgreSQL type casts like ::text
		if "::" in s:
			s = s.split("::", 1)[0]
		return s.strip().lower()

	def _cleanup_nulls(self, schema: str, table: str, column: str) -> None:
		"""
		Delete rows where the given column is NULL to satisfy NOT NULL constraints.
		"""
		try:
			rows = self.execute_query(
				f'SELECT COUNT(*) AS cnt FROM "{schema}"."{table}" WHERE "{column}" IS NULL;'
			) or []
			cnt = int(rows[0]["cnt"]) if rows else 0
			if cnt <= 0:
				return
			self.execute_query(
				f'DELETE FROM "{schema}"."{table}" WHERE "{column}" IS NULL;'
			)
			logger.warning("Deleted %s rows with NULL %s.%s.%s to satisfy NOT NULL.", cnt, schema, table, column)
		except Exception:
			logger.exception("Failed to cleanup NULLs for %s.%s.%s", schema, table, column)

	def _enum_values_sql(self, values: list) -> str:
		escaped = []
		for v in values:
			s = str(v)
			escaped.append("'" + s.replace("'", "''") + "'")
		return ", ".join(escaped)

	def _expected_type_signature(self, c: dict) -> tuple:
		t = str(c["type"]).lower()
		if t in {"varchar", "character varying"}:
			return ("varchar", int(c.get("length", 0)))
		if t in {"char", "character"}:
			return ("char", int(c.get("length", 0)))
		if t in {"numeric", "decimal"}:
			prec = c.get("precision")
			scale = c.get("scale")
			return ("numeric", int(prec) if prec is not None else None, int(scale) if scale is not None else None)
		if t in {"int", "integer"}:
			return ("integer",)
		return (t,)

	def _existing_type_signature(self, info: dict) -> tuple:
		udt = (info.get("udt_name") or "").lower()
		data_type = (info.get("data_type") or "").lower()

		if udt in {"varchar", "bpchar"}:
			base = "varchar" if udt == "varchar" else "char"
			return (base, int(info.get("character_maximum_length") or 0))
		if udt in {"numeric"}:
			return ("numeric", info.get("numeric_precision"), info.get("numeric_scale"))
		if udt in {"int4"}:
			return ("integer",)
		if udt in {"int8"}:
			return ("bigint",)
		if udt in {"bool"}:
			return ("boolean",)
		if udt in {"timestamptz"}:
			return ("timestamptz",)
		if udt in {"timestamp"}:
			return ("timestamp",)
		if udt:
			return (udt,)
		return (data_type or "",)

	def _column_type_sql(self, c: dict) -> str:
		"""
		Map config to a SQL type string. Keep it explicit and predictable.
		"""
		t = str(c["type"]).lower()

		if t in {"uuid", "text", "boolean", "timestamp", "timestamptz", "date", "json", "jsonb"}:
			return t

		if t in {"varchar", "character varying"}:
			n = c.get("length")
			if not n:
				raise ValueError(f"varchar column '{c['name']}' missing length")
			return f"varchar({int(n)})"

		if t in {"char", "character"}:
			n = c.get("length")
			if not n:
				raise ValueError(f"char column '{c['name']}' missing length")
			return f"char({int(n)})"

		if t in {"int", "integer"}:
			return "integer"

		if t in {"bigint"}:
			return "bigint"

		if t in {"numeric", "decimal"}:
			prec = c.get("precision")
			scale = c.get("scale")
			if prec is not None and scale is not None:
				return f"numeric({int(prec)},{int(scale)})"
			if prec is not None:
				return f"numeric({int(prec)})"
			return "numeric"

		if c.get("raw_type"):
			return str(c["raw_type"])

		raise ValueError(f"Unsupported column type: {c['type']} (column {c.get('name')})")
