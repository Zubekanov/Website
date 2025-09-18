import logging
from math import ceil
from psycopg2 import sql
from psycopg2.pool import ThreadedConnectionPool
from util.fcr.file_config_reader import FileConfigReader

logger = logging.getLogger(__name__)

fcr = FileConfigReader()
psql_conf = fcr.load_config('config/psql.conf')

class PSQLClient:
	_instance = None
	_initialised = False

	def __new__(cls, *args, **kwargs):
		if cls._instance is None:
			cls._instance = super(PSQLClient, cls).__new__(cls)
		return cls._instance

	def __init__(self, database: str | None = None, user: str | None = None, minconn: int = 1, maxconn: int = 10):
		if PSQLClient._initialised:
			return
		PSQLClient._initialised = True

		# Database hosted locally so host, port, and password are omitted.
		self.database = database or psql_conf.get("DATABASE", "postgres")
		self.user     = user or     psql_conf.get("USER", "postgres")

		self.pool = ThreadedConnectionPool(
			minconn, maxconn,
			database=self.database,
			user=self.user
		)

		# TODO Reimplement schema checking and/or execution.

	def _get_conn(self):
		"""Get a connection from the pool."""
		return self.pool.getconn()

	def _put_conn(self, conn):
		"""Return a connection to the pool."""
		self.pool.putconn(conn)

	def _execute(self, query, params: list | None = None) -> list[dict] | None:
		conn = self._get_conn()
		try:
			with conn.cursor() as cur:
				cur.execute(query, params or [])
				status = (cur.statusmessage or "").upper()
				has_result = cur.description is not None

				if has_result:
					colnames = [d[0] for d in cur.description]
					rows = cur.fetchall()
					# Commit if this was DML with RETURNING
					if status.startswith(("INSERT", "UPDATE", "DELETE", "MERGE")):
						conn.commit()
					return [dict(zip(colnames, r)) for r in rows]
				else:
					# No result set (DDL/DML without RETURNING)
					conn.commit()
					return None
		except Exception:
			conn.rollback()
			raise
		finally:
			self._put_conn(conn)

	# ---------- Database-level helpers (autocommit required) ----------
	def _execute_autocommit(self, query, params=None):
		"""
		Execute a statement that must run outside a transaction (e.g., CREATE/DROP DATABASE).
		Returns None or list[dict] like _execute.
		"""
		conn = self._get_conn()
		prev = getattr(conn, "autocommit", False)
		try:
			conn.autocommit = True
			with conn.cursor() as cur:
				cur.execute(query, params or [])
				if cur.description:
					names = [d[0] for d in cur.description]
					rows  = cur.fetchall()
					return [dict(zip(names, r)) for r in rows]
				return None
		finally:
			conn.autocommit = prev
			self._put_conn(conn)

	def database_exists(self, db_name: str) -> bool:
		q = "SELECT 1 FROM pg_database WHERE datname = %s;"
		return bool(self._execute(q, [db_name]))

	def create_database(self, db_name: str, exists_ok: bool = True) -> bool:
		if exists_ok and self.database_exists(db_name):
			return False
		q = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name))
		self._execute_autocommit(q)
		return True

	def drop_database(self, db_name: str, missing_ok: bool = True) -> bool:
		if not missing_ok and not self.database_exists(db_name):
			raise ValueError(f"Database not found: {db_name}")
		q = sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name))
		self._execute_autocommit(q)
		return True

	# ---------- Schema helpers ----------
	def schema_exists(self, schema: str) -> bool:
		q = "SELECT 1 FROM pg_namespace WHERE nspname = %s;"
		return bool(self._execute(q, [schema]))

	def list_schemas(self, exclude_system: bool = True) -> list[str]:
		if exclude_system:
			q = """
				SELECT schema_name
				FROM information_schema.schemata
				WHERE schema_name NOT IN ('information_schema','pg_catalog')
				AND schema_name NOT LIKE 'pg_toast%%'
				AND schema_name NOT LIKE 'pg_temp%%'
				ORDER BY schema_name;
			"""
			return [r["schema_name"] for r in self._execute(q)]
		else:
			q = "SELECT schema_name FROM information_schema.schemata ORDER BY schema_name;"
			return [r["schema_name"] for r in self._execute(q)]

	def create_schema(self, schema: str, exists_ok: bool = True) -> None:
		if exists_ok:
			q = sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
		else:
			q = sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema))
		self._execute(q)

	def drop_schema(self, schema: str, cascade: bool = False, missing_ok: bool = True) -> None:
		q = sql.SQL("DROP SCHEMA {} {} {}").format(
			sql.SQL("IF EXISTS") if missing_ok else sql.SQL(""),
			sql.Identifier(schema),
			sql.SQL("CASCADE") if cascade else sql.SQL("")
		)
		self._execute(q)

	def ensure_schema(self, schema: str) -> None:
		self.create_schema(schema, exists_ok=True)

	# ---------- Table & column helpers ----------
	def table_exists(self, schema: str, table: str) -> bool:
		q = """
			SELECT 1
			FROM information_schema.tables
			WHERE table_schema = %s AND table_name = %s;
		"""
		return bool(self._execute(q, [schema, table]))

	def list_tables(self, schema: str = "public") -> list[str]:
		q = """
			SELECT table_name
			FROM information_schema.tables
			WHERE table_schema = %s AND table_type = 'BASE TABLE'
			ORDER BY table_name;
		"""
		return [r["table_name"] for r in self._execute(q, [schema])]

	def get_table_columns(self, schema: str, table: str) -> list[str]:
		q = """
			SELECT column_name
			FROM information_schema.columns
			WHERE table_schema = %s AND table_name = %s
			ORDER BY ordinal_position;
		"""
		rows = self._execute(q, [schema, table]) or []
		return [r["column_name"] for r in rows]

	def column_exists(self, schema: str, table: str, column: str) -> bool:
		q = """
			SELECT 1
			FROM information_schema.columns
			WHERE table_schema = %s AND table_name = %s AND column_name = %s;
		"""
		return bool(self._execute(q, [schema, table, column]))

	def create_table(
		self,
		schema: str,
		table: str,
		columns: dict[str, str],               # e.g. {"id":"BIGSERIAL PRIMARY KEY","name":"TEXT NOT NULL"}
		constraints: list[str] | None = None,  # e.g. ["UNIQUE (name)"]
		if_not_exists: bool = True,
		temporary: bool = False
	) -> None:
		if not columns:
			raise ValueError("columns must be a non-empty dict of {name: SQL type/constraint}.")
		col_bits = []
		for name, type_sql in columns.items():
			if not isinstance(type_sql, str) or not type_sql.strip():
				raise ValueError(f"Invalid type/constraint for column '{name}'.")
			col_bits.append(sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(type_sql)))
		if constraints:
			col_bits.extend(sql.SQL(c) for c in constraints)

		q = sql.SQL("CREATE {temp} TABLE {ine} {sch}.{tbl} ({cols})").format(
			temp=sql.SQL("TEMP") if temporary else sql.SQL(""),
			ine=sql.SQL("IF NOT EXISTS") if if_not_exists else sql.SQL(""),
			sch=sql.Identifier(schema),
			tbl=sql.Identifier(table),
			cols=sql.SQL(", ").join(col_bits)
		)
		self._execute(q)

	def drop_table(self, schema: str, table: str, cascade: bool = False, missing_ok: bool = True) -> None:
		q = sql.SQL("DROP TABLE {} {}.{} {}").format(
			sql.SQL("IF EXISTS") if missing_ok else sql.SQL(""),
			sql.Identifier(schema),
			sql.Identifier(table),
			sql.SQL("CASCADE") if cascade else sql.SQL("")
		)
		self._execute(q)

	def ensure_table(self, schema: str, table: str, columns: dict[str, str], constraints: list[str] | None = None) -> None:
		self.create_table(schema, table, columns, constraints, if_not_exists=True)

	# ---------- Index helpers ----------
	def index_exists(self, schema: str, index_name: str) -> bool:
		q = """
			SELECT 1
			FROM pg_indexes
			WHERE schemaname = %s AND indexname = %s;
		"""
		return bool(self._execute(q, [schema, index_name]))

	def create_index(
		self,
		schema: str,
		table: str,
		index_name: str,
		columns: list[str],
		unique: bool = False,
		if_not_exists: bool = True
	) -> None:
		if not columns:
			raise ValueError("columns must be a non-empty list of column names.")
		prefix = sql.SQL("CREATE {} INDEX {} {}").format(
			sql.SQL("UNIQUE") if unique else sql.SQL(""),
			sql.SQL("IF NOT EXISTS") if if_not_exists else sql.SQL(""),
			sql.Identifier(index_name),
		)
		cols = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
		q = prefix + sql.SQL(" ON {}.{} ({})").format(sql.Identifier(schema), sql.Identifier(table), cols)
		self._execute(q)

	def drop_index(self, schema: str, index_name: str, missing_ok: bool = True) -> None:
		q = sql.SQL("DROP INDEX {} {}.{}").format(
			sql.SQL("IF EXISTS") if missing_ok else sql.SQL(""),
			sql.Identifier(schema),
			sql.Identifier(index_name),
		)
		self._execute(q)

	def _split_qualified(self, qname) -> tuple[str|None, str]:
		"""
		Accepts 'schema.table', 'table', or ('schema','table').
		Returns (schema_or_None, table).
		"""
		if isinstance(qname, (tuple, list)):
			if len(qname) == 2:
				return (str(qname[0]), str(qname[1]))
			if len(qname) == 1:
				return (None, str(qname[0]))
			raise ValueError("qname tuple/list must be length 1 or 2")
		s = str(qname).strip()
		if "." in s:
			schema, table = s.split(".", 1)
			return (schema.strip('"'), table.strip('"'))
		return (None, s.strip('"'))

	def _ident_qualified(self, qname) -> sql.Composed:
		"""
		Build a properly quoted identifier for an optional schema-qualified name.
		"""
		schema, table = self._split_qualified(qname)
		if schema:
			return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))
		return sql.Identifier(table)

	def insert_row(self, table: str, data: dict) -> dict | None:
		if not data:
			raise ValueError("Data dictionary is empty.")
		columns = list(data.keys())
		values  = list(data.values())

		query = sql.SQL(
			"INSERT INTO {tbl} ({fields}) VALUES ({placeholders}) RETURNING *"
		).format(
			tbl=self._ident_qualified(table),
			fields=sql.SQL(', ').join(sql.Identifier(c) for c in columns),
			placeholders=sql.SQL(', ').join(sql.Placeholder() for _ in columns),
		)
		result = self._execute(query, values)
		return result[0] if result else None

	def _paged_execute(
		self, query, params: list | None = None, page_limit: int = 50, page_num: int = 0,
		order_by: str | None = None, order_dir: str = "ASC", tiebreaker: str | None = None,
		base_qualifier: str | sql.Composable | None = None,
	) -> list[dict]:
		# --- validate paging args ---
		if not isinstance(page_limit, int) or page_limit <= 0:
			raise ValueError("page_limit must be a positive integer.")
		if not isinstance(page_num, int) or page_num < 0:
			raise ValueError("page_num must be a non-negative integer.")

		params = list(params or [])
		limit  = page_limit
		offset = page_limit * page_num

		conn = self._get_conn()
		try:
			if isinstance(query, str):
				base = query.strip().rstrip(';')
			else:
				base = query.as_string(conn).strip().rstrip(';')

			head = base.lstrip().split(None, 1)[0].upper() if base else ""
			if head not in {"SELECT", "WITH"}:
				raise ValueError(f"_paged_execute expects a SELECT/CTE, got: {head or 'EMPTY'}")

			# Resolve qualifier (schema/table or alias) if provided
			qual_str = None
			if base_qualifier:
				if isinstance(base_qualifier, sql.Composable):
					qual_str = base_qualifier.as_string(conn)
				else:
					qual_str = str(base_qualifier)

			order_sql = ""
			if order_by:
				dir_up = str(order_dir).upper()
				if dir_up not in {"ASC", "DESC"}:
					raise ValueError("order_dir must be 'ASC' or 'DESC'")
				ob = sql.Identifier(order_by).as_string(conn)
				ob_qual = f"{qual_str}.{ob}" if qual_str else ob
				order_sql = f" ORDER BY {ob_qual} {dir_up}"
				if tiebreaker and tiebreaker != order_by:
					tb = sql.Identifier(tiebreaker).as_string(conn)
					tb_qual = f"{qual_str}.{tb}" if qual_str else tb
					order_sql += f", {tb_qual} ASC"

			final_sql = f"{base}{order_sql} LIMIT %s OFFSET %s;"
		finally:
			self._put_conn(conn)

		return self._execute(final_sql, params + [limit, offset]) or []


	def _get_table_columns(self, table: str) -> list[str]:
		schema, tbl = self._split_qualified(table)
		if schema:
			q = """
				SELECT column_name
				FROM information_schema.columns
				WHERE table_schema = %s AND table_name = %s
				ORDER BY ordinal_position;
			"""
			rows = self._execute(q, [schema, tbl]) or []
		else:
			q = """
				SELECT column_name
				FROM information_schema.columns
				WHERE table_name = %s
				ORDER BY ordinal_position;
			"""
			rows = self._execute(q, [tbl]) or []
		return [r["column_name"] for r in rows]

	def get_rows_with_filters(
		self,
		table: str,
		equalities: dict | None = None,             # {"col": val, ...} on the base table only
		raw_conditions: str | list[str] | None = None,  # SQL fragments (without 'WHERE')
		raw_params: list | None = None,              # params for placeholders in raw_conditions, in order
		joins: list[tuple[str, str, str]] | None = None,  # [(join_type, table_expr, on_clause), ...]
		page_limit: int = 50,
		page_num: int = 0,
		order_by: str | None = None,                 # base-table column to order by
		order_dir: str = "ASC"                       # 'ASC' | 'DESC'
	) -> tuple[list[dict], int]:
		"""
		Unified row fetcher with equality filters, raw conditions, joins, ordering, and pagination.

		Returns: (rows, total_pages). If no matches, returns ([], 0).

		Notes:
		- `equalities` are validated against the *base table* columns only.
		- `raw_conditions` lets you express arbitrary predicates (including join columns);
		pass parameters via `raw_params`.
		- `joins` items are appended verbatim: e.g. ("LEFT JOIN", "schema.other o", "o.fk = t.id").
		- Ordering is on a base-table column; if not provided, uses 'id' if present, otherwise the base table's first column.
		- Uses a stable tiebreaker on 'id' (if available and different from order_by) to minimise page overlap with OFFSET.
		"""
		# --- validate paging args ---
		if not isinstance(page_limit, int) or page_limit <= 0:
			raise ValueError("page_limit must be a positive integer.")
		if not isinstance(page_num, int) or page_num < 0:
			raise ValueError("page_num must be a non-negative integer.")

		# --- discover base-table columns & validate equality filters ---
		valid_columns = self._get_table_columns(table)
		if not valid_columns:
			raise ValueError(f"Table '{table}' does not exist.")

		if equalities:
			invalid = [k for k in equalities if k not in valid_columns]
			if invalid:
				raise ValueError(f"Invalid columns for condition: {invalid}")

		# --- order by & tiebreaker (base-table columns only) ---
		if order_by is not None:
			if order_by not in valid_columns:
				raise ValueError(f"Unknown order_by column: {order_by}")
			order_col = order_by
		else:
			order_col = "id" if "id" in valid_columns else valid_columns[0]

		dir_up = str(order_dir).upper()
		if dir_up not in {"ASC", "DESC"}:
			raise ValueError("order_dir must be 'ASC' or 'DESC'")

		tiebreaker = "id" if ("id" in valid_columns and order_col != "id") else None

		from_clause = sql.SQL(" FROM ") + self._ident_qualified(table)
		if joins:
			for j in joins:
				if isinstance(j, str):
					# Accept a fully-formed raw JOIN fragment
					from_clause += sql.SQL(" ") + sql.SQL(j)
				else:
					try:
						join_type, table_expr, on_clause = j
					except Exception:
						raise ValueError("joins entries must be either raw JOIN strings or 3-tuples (join_type, table_expr, on_clause).")
					from_clause += sql.SQL(" {} {} ON {}").format(
						sql.SQL(join_type),
						sql.SQL(table_expr),
						sql.SQL(on_clause)
					)

		where_parts = []
		params = []

		if equalities:
			items = list(equalities.items())  # preserve param order
			where_parts.extend(
				sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder())
				for k, _ in items
			)
			params.extend(v for _, v in items)

		if raw_conditions:
			if isinstance(raw_conditions, str):
				where_parts.append(sql.SQL(raw_conditions))
			else:
				for frag in raw_conditions:
					where_parts.append(sql.SQL(frag))
			if raw_params:
				params.extend(list(raw_params))

		where_sql = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where_parts) if where_parts else sql.SQL("")

		# --- total count (counts result rows after joins) ---
		q_count = sql.SQL("SELECT COUNT(*) AS cnt") + from_clause + where_sql + sql.SQL(";")
		count_result = self._execute(q_count, params)
		total = int(count_result[0]["cnt"]) if count_result else 0
		if total == 0:
			return [], 0

		total_pages = ceil(total / page_limit)

		# --- SELECT page (delegate pagination to _paged_execute) ---
		q_select = sql.SQL("SELECT *") + from_clause + where_sql
		rows = self._paged_execute(
			q_select,
			params,
			page_limit=page_limit,
			page_num=page_num,
			order_by=order_col,
			order_dir=dir_up,
			tiebreaker=tiebreaker,
			base_qualifier=self._ident_qualified(table),
		) or []

		return rows, total_pages

	def delete_rows_with_filters(
		self,
		table: str,
		equalities: dict | None = None,                 # {"col": val, ...} on the base table only
		raw_conditions: str | list[str] | None = None,  # SQL fragments for WHERE (without 'WHERE')
		raw_params: list | None = None,                 # params for placeholders in raw_conditions
		joins: list[tuple[str, str, str]] | None = None # [(join_type, table_expr, on_clause), ...]
	) -> int:
		"""
		Delete rows from `table` filtered by equalities and/or raw conditions, with optional joins.

		Returns:
			int: number of rows deleted.

		Parameters:
			- table: base table name (no alias).
			- equalities: dict of {base_table_column: value}. Validated against the base table's columns.
			- raw_conditions: string or list of strings appended to WHERE (caller is responsible for correctness).
			- raw_params: parameters for placeholders used inside raw_conditions (in order).
			- joins: list of (join_type, table_expr, on_clause). join_type is ignored for DELETE;
					tables in `table_expr` are listed in USING and each `on_clause` is ANDed into WHERE.
		"""
		# Require some filter
		if not equalities and not raw_conditions:
			raise ValueError("Provide at least one of 'equalities' or 'raw_conditions'.")

		# Validate base-table columns for equalities
		valid_columns = self._get_table_columns(table)
		if not valid_columns:
			raise ValueError(f"Table '{table}' does not exist.")
		if equalities:
			invalid = [k for k in equalities if k not in valid_columns]
			if invalid:
				raise ValueError(f"Invalid columns for condition: {invalid}")

		# USING clause from joins (ignore join_type for DELETE)
		using_clause = sql.SQL("")
		on_parts = []
		if joins:
			using_bits = []
			for _join_type, table_expr, on_clause in joins:
				using_bits.append(sql.SQL(table_expr))          # raw: may include schema and alias
				on_parts.append(sql.SQL(on_clause))             # raw: predicate added to WHERE
			if using_bits:
				using_clause = sql.SQL(" USING ") + sql.SQL(", ").join(using_bits)

		# WHERE parts: equalities then join on-clauses then raw conditions
		where_parts = []
		params = []

		if equalities:
			items = list(equalities.items())  # preserve param order
			where_parts.extend(
				sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder())
				for k, _ in items
			)
			params.extend(v for _, v in items)

		# join ON predicates
		where_parts.extend(on_parts)

		# raw conditions
		if raw_conditions:
			if isinstance(raw_conditions, str):
				where_parts.append(sql.SQL(raw_conditions))
			else:
				where_parts.extend(sql.SQL(frag) for frag in raw_conditions)
			if raw_params:
				params.extend(list(raw_params))

		if not where_parts:
			# Defensive: if joins provided but no predicates, this would wipe whole table
			raise ValueError("No WHERE predicates built. Refusing to delete without filters.")

		where_sql = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where_parts)

		# DELETE ... USING ... WHERE ... RETURNING 1
		q = sql.SQL("DELETE FROM ") + self._ident_qualified(table)
		q = q + using_clause + where_sql + sql.SQL(" RETURNING 1;")

		result = self._execute(q, params)
		return len(result) if result else 0


	def update_rows_with_equalities(self, table: str, updates: dict, equalities: dict) -> int:
		"""
		Update rows matching the given equality conditions with the provided updates.
		- Returns the number of rows updated.
		"""
		if not updates:
			raise ValueError("Updates dictionary is empty.")
		if not equalities:
			raise ValueError("Conditions dictionary is empty.")

		valid_columns = self._get_table_columns(table)
		if not valid_columns:
			raise ValueError(f"Table '{table}' does not exist.")
		
		invalid_updates = [k for k in updates if k not in valid_columns]
		if invalid_updates:
			raise ValueError(f"Invalid columns for update: {invalid_updates}")
		
		invalid_conditions = [k for k in equalities if k not in valid_columns]
		if invalid_conditions:
			raise ValueError(f"Invalid columns for condition: {invalid_conditions}")

		update_items = list(updates.items())
		where_items = list(equalities.items())

		set_clauses = [
			sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder())
			for k, _ in update_items
		]
		where_clauses = [
			sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder())
			for k, _ in where_items
		]

		set_sql = sql.SQL(", ").join(set_clauses)
		where_sql = sql.SQL(" AND ").join(where_clauses)

		set_params = [v for _, v in update_items]
		where_params = [v for _, v in where_items]

		query = sql.SQL("UPDATE {tbl} SET {sets} WHERE {conds} RETURNING *;").format(
			tbl=self._ident_qualified(table),
			sets=set_sql,
			conds=where_sql
		)


		result = self._execute(query, set_params + where_params)
		return len(result) if result else 0
	
	def update_rows_with_filters(
		self,
		table: str,
		updates: dict,                                  # {base_table_col: value}
		equalities: dict | None = None,                 # {base_table_col: value}
		raw_conditions: str | list[str] | None = None,  # SQL fragments (no 'WHERE')
		raw_params: list | None = None,                 # params for raw_conditions placeholders
		joins: list[tuple[str, str, str]] | None = None # [(join_type, table_expr, on_clause), ...]
	) -> int:
		"""
		Update rows in `table` using:
		- validated equality predicates on the base table,
		- optional raw SQL predicates,
		- optional joins (Postgres UPDATE ... FROM ... pattern).

		Returns the number of rows updated.
		"""
		# Validate inputs
		if not updates:
			raise ValueError("Updates dictionary is empty.")

		valid_columns = self._get_table_columns(table)
		if not valid_columns:
			raise ValueError(f"Table '{table}' does not exist.")

		invalid_updates = [k for k in updates if k not in valid_columns]
		if invalid_updates:
			raise ValueError(f"Invalid columns for update: {invalid_updates}")

		if equalities:
			invalid_conds = [k for k in equalities if k not in valid_columns]
			if invalid_conds:
				raise ValueError(f"Invalid columns for condition: {invalid_conds}")

		# SET clause
		update_items = list(updates.items())
		set_clauses = [
			sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder())
			for k, _ in update_items
		]
		set_sql = sql.SQL(", ").join(set_clauses)
		set_params = [v for _, v in update_items]

		# FROM (joins) and ON parts
		from_clause = sql.SQL("")
		on_parts = []
		if joins:
			from_bits = []
			for _join_type, table_expr, on_clause in joins:
				from_bits.append(sql.SQL(table_expr))   # may include schema and alias
				on_parts.append(sql.SQL(on_clause))     # added to WHERE
			if from_bits:
				from_clause = sql.SQL(" FROM ") + sql.SQL(", ").join(from_bits)

		# WHERE parts: equalities, join ON predicates, then raw conditions
		where_parts = []
		params = []

		if equalities:
			where_items = list(equalities.items())
			where_parts.extend(
				sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder())
				for k, _ in where_items
			)
			params.extend(v for _, v in where_items)

		# join ON predicates
		where_parts.extend(on_parts)

		# raw conditions
		if raw_conditions:
			if isinstance(raw_conditions, str):
				where_parts.append(sql.SQL(raw_conditions))
			else:
				where_parts.extend(sql.SQL(frag) for frag in raw_conditions)
			if raw_params:
				params.extend(list(raw_params))

		if not where_parts:
			raise ValueError("No WHERE predicates built. Refusing to update without filters.")

		where_sql = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where_parts)

		# UPDATE ... SET ... FROM ... WHERE ... RETURNING 1
		q = sql.SQL("UPDATE {tbl} SET {sets}").format(
			tbl=self._ident_qualified(table),
			sets=set_sql
		)
		q = q + from_clause + where_sql + sql.SQL(" RETURNING 1;")

		# If you prefer your private wrapper, swap to self._execute
		result = self._execute(q, set_params + params)
		return len(result) if result else 0