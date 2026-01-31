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
		expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

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
	
	def validate_verification_token(self, token: str) -> bool:
		if not token or not str(token).strip():
			return False
		token_hash = self._hash_verification_token(token.strip())

		pending_users = self.get_pending_user({"verification_token_hash": token_hash})
		if not pending_users: return False
		
		valid = any(
			pending_user.get("token_expires_at") and pending_user["token_expires_at"].replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
			for pending_user in pending_users
		)

		if not valid: return False

		pending_user = next(
			(pending_user for pending_user in pending_users if pending_user["token_expires_at"].replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)),
			None
		)
		
		user = self.get_user({"email": pending_user["email"]})
		if user: return False

		status, message = self.insert_user({
			"id": pending_user["id"],
			"email": pending_user["email"],
			"first_name": pending_user["first_name"],
			"last_name": pending_user["last_name"],
			"password_hash": pending_user["password_hash"],
		})

		return status
	
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
	
	def check_session_token(self, raw_token: str) -> dict | None:
		if not raw_token:
			return None

		token_hash = self._hash_session_token(raw_token)

		# Cache lookup
		cached = session_cache.get(token_hash)
		if cached is not None:
			logging.info("Session token cache hit.")
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
		user = self.get_user({"id": sess["user_id"]})[0]
		exp = sess["expires_at"]
		if exp.tzinfo is None:
			exp = exp.replace(tzinfo=timezone.utc)
		ttl = int((exp - datetime.now(timezone.utc)).total_seconds())
		ttl = max(1, min(ttl, 300))

		session_cache.set(token_hash, user, ttl_seconds=ttl)
		return user
	
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
 
	@property
	def client(self):
		return self._client
	
	def verify_tables(self, safe_mode: bool = True) -> None:
		tables_dir = os.path.join(os.path.dirname(__file__), "tables")
		json_files = glob.glob(os.path.join(tables_dir, "*.json"))

		for json_file in json_files:
			table_config_name = os.path.basename(json_file)
			self.verify_table(table_config_name, safe_mode=safe_mode)

	def verify_table(self, table_config_name: str, *, safe_mode: bool = True) -> None:
		"""
		safe_mode=True:
			- only additive changes (create table, add columns, add indexes, add constraints where possible)
			- does NOT drop columns, does NOT change types, does NOT change nullability/defaults
		"""
		table_config = fcr.find(table_config_name)
		logger.info("Verifying table(s) from config: %s", table_config_name)
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

		has_changes = self._alter_table_additive(schema, table, columns_cfg, indexes_cfg, safe_mode=safe_mode)
		if not has_changes:
			logger.info("✅ Verified table %s.%s (no changes)", schema, table)
		else:
			logger.info("☑️ Verified table %s.%s", schema, table)

		logger.info("Table initialised with columns: %s", list(self.client.get_column_info(schema, table).keys()))
		
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
