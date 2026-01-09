import logging
from psycopg2 import sql

from sql.psql_client import PSQLClient
from util.fcr.file_config_reader import FileConfigReader
import os
import glob

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

	@property
	def client(self):
		return self._client
	
	def verify_tables(self):
		tables_dir = os.path.join(os.path.dirname(__file__), "tables")
		json_files = glob.glob(os.path.join(tables_dir, "*.json"))

		for json_file in json_files:
			table_config_name = os.path.basename(json_file)
			self.verify_table(table_config_name)

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

		# Table-level unique constraints (more reliable than relying on indexes for uniqueness)
		for c in columns_cfg:
			if c.get("unique", False) and not c.get("primary_key", False):
				con_name = f"{table}_{c['name']}_key"
				constraints.append(f'CONSTRAINT "{con_name}" UNIQUE ("{c["name"]}")')

		# Foreign keys (table-level constraints)
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

	def _ensure_indexes(self, schema: str, table: str, indexes_cfg: list[dict], columns_cfg: list[dict]) -> None:
		existing = {r["indexname"] for r in self.client.list_indexes(schema, table)}

		for c in columns_cfg:
			if c.get("index", False):
				idx_name = f"{table}_{c['name']}_idx"
				if idx_name not in existing:
					self.client.create_index(schema, table, idx_name, [c["name"]], unique=False, if_not_exists=True)
					logger.info("Created index %s on %s.%s(%s)", idx_name, schema, table, c["name"])

		for idx in indexes_cfg:
			name = idx["name"]
			if name in existing:
				continue
			cols = idx["columns"]
			unique = bool(idx.get("unique", False))
			self.client.create_index(schema, table, name, cols, unique=unique, if_not_exists=True)
			logger.info("Created index %s on %s.%s(%s)", name, schema, table, ", ".join(cols))

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
