import logging
from psycopg2 import sql
from psycopg2.pool import ThreadedConnectionPool
from util.config_reader import ConfigReader

logger = logging.getLogger(__name__)

# Load database config
database_config = ConfigReader.get_key_value_config("database.config")
schema_json = ConfigReader.get_json("schema.json")

class PSQLClient:
	"""
	Postgres helper using a connection pool.
	"""
	_instance     = None
	_initialised  = False

	def __new__(cls, *args, **kwargs):
		# only create the object once
		if cls._instance is None:
			cls._instance = super(PSQLClient, cls).__new__(cls)
		return cls._instance

	def __init__(self, host=None, port=None, database=None, user=None, password=None, minconn=1, maxconn=10):
		if PSQLClient._initialised:
			return
		PSQLClient._initialised = True

		self.host     = host or database_config.get("HOST", "localhost")
		self.port     = port or database_config.get("PORT", 5432)
		self.database = database or database_config.get("DATABASE", "postgres")
		self.user     = user or database_config.get("USER", "postgres")
		self.password = password or database_config.get("PASSWORD", "")

		self.pool = ThreadedConnectionPool(
			minconn, maxconn,
			database=self.database,
			user=self.user
		)

		try:
			schema_statements = ConfigReader.get_sql("schema.sql")
			for stmt in schema_statements:
				if stmt.strip():  # skip empties
					self.execute(query=stmt)
			logger.debug("Database schema initialized successfully.")
		except Exception as e:
			logger.exception("Error initializing database schema: %s", e)
			raise

	def close(self):
		"""Close all pooled connections."""
		if self.pool:
			self.pool.closeall()

	def _get_conn(self):
		"""Get a connection from the pool."""
		return self.pool.getconn()

	def _put_conn(self, conn):
		"""Return a connection to the pool."""
		self.pool.putconn(conn)

	def execute(self, query, params=None):
		"""
		Execute a query safely; returns list of dicts for SELECTs, commits otherwise.
		"""
		conn = self._get_conn()
		try:
			with conn.cursor() as cur:
				cur.execute(query, params or [])
				if cur.description:
					# SELECT
					colnames = [d[0] for d in cur.description]
					rows = cur.fetchall()
					return [dict(zip(colnames, r)) for r in rows]
				else:
					# DML/DDL
					conn.commit()
					return None
		except Exception:
			conn.rollback()
			raise
		finally:
			self._put_conn(conn)

	def list_databases(self):
		"""Return non-template database names."""
		return [row['datname'] for row in self.execute(
			"SELECT datname FROM pg_database WHERE datistemplate = false;"
		)]

	def list_tables(self, schema="public"):
		"""Return tables in the given schema."""
		return [row['tablename'] for row in self.execute(
			sql.SQL("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = %s;"),
			[schema]
		)]

	def get_records(self, table, limit=100):
		"""Fetch up to `limit` rows from `table`."""
		query = sql.SQL("SELECT * FROM {tbl} LIMIT %s;").format(
			tbl=sql.Identifier(table)
		)
		return self.execute(query, [limit])

	def drop_table(self, table, schema="public"):
		"""Drop a table if it exists."""
		q = sql.SQL("DROP TABLE IF EXISTS {schema}.{table} CASCADE;").format(
			schema=sql.Identifier(schema),
			table=sql.Identifier(table)
		)
		self.execute(q)

	def create_database(self, db_name):
		"""Create a new database."""
		q = sql.SQL("CREATE DATABASE {db};").format(
			db=sql.Identifier(db_name)
		)
		self.execute(q)

	def drop_database(self, db_name):
		"""Drop a database if it exists."""
		q = sql.SQL("DROP DATABASE IF EXISTS {db};").format(
			db=sql.Identifier(db_name)
		)
		self.execute(q)

	def _get_table_columns(self, table, schema='public'):
		"""Return a list of valid column names for a given table."""
		result = self.execute(
			"""
			SELECT column_name
			FROM information_schema.columns
			WHERE table_schema = %s AND table_name = %s;
			""",
			[schema, table]
		)
		return [r['column_name'] for r in result]

	def insert_row(self, table, data: dict, returning: list[str] = None):
		"""
		Insert a row into `table` using column-value mapping from `data`.
		Optionally return specified columns (e.g. ['uid'] or ['*']).
		"""
		if not data:
			raise ValueError("Data dictionary is empty.")

		# Check table and columns
		valid_columns = self._get_table_columns(table)
		if not valid_columns:
			raise ValueError(f"Table '{table}' does not exist.")
		invalid = [k for k in data if k not in valid_columns]
		if invalid:
			raise ValueError(f"Invalid columns for insert: {invalid}")

		columns = [sql.Identifier(k) for k in data.keys()]
		values = list(data.values())
		placeholders = [sql.Placeholder() for _ in values]

		q = sql.SQL("INSERT INTO {table} ({fields}) VALUES ({placeholders})").format(
			table=sql.Identifier(table),
			fields=sql.SQL(', ').join(columns),
			placeholders=sql.SQL(', ').join(placeholders)
		)

		if returning:
			# allow ['*'] or list of column names
			if returning == ["*"]:
				q += sql.SQL(" RETURNING *")
			else:
				ret_cols = [sql.Identifier(c) for c in returning]
				q += sql.SQL(" RETURNING ") + sql.SQL(', ').join(ret_cols)

		result = self.execute(q, values)
		return result  # list of dicts if RETURNING was used, else None


	def get_rows_by_conditions(self, table, conditions: dict):
		"""Get rows from `table` where each key-value in `conditions` is matched."""
		if not conditions:
			raise ValueError("Conditions dictionary is empty.")

		valid_columns = self._get_table_columns(table)
		if not valid_columns:
			raise ValueError(f"Table '{table}' does not exist.")
		invalid = [k for k in conditions if k not in valid_columns]
		if invalid:
			raise ValueError(f"Invalid columns for condition: {invalid}")

		where_clauses = [
			sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder())
			for k in conditions.keys()
		]
		q = sql.SQL("SELECT * FROM {table} WHERE {conds};").format(
			table=sql.Identifier(table),
			conds=sql.SQL(" AND ").join(where_clauses)
		)
		return self.execute(q, list(conditions.values()))

	def delete_rows_by_conditions(self, table, conditions: dict):
		"""Delete rows from `table` where each key-value in `conditions` is matched."""
		if not conditions:
			raise ValueError("Conditions dictionary is empty.")

		valid_columns = self._get_table_columns(table)
		if not valid_columns:
			raise ValueError(f"Table '{table}' does not exist.")
		invalid = [k for k in conditions if k not in valid_columns]
		if invalid:
			raise ValueError(f"Invalid columns for condition: {invalid}")

		where_clauses = [
			sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder())
			for k in conditions.keys()
		]
		q = sql.SQL("DELETE FROM {table} WHERE {conds};").format(
			table=sql.Identifier(table),
			conds=sql.SQL(" AND ").join(where_clauses)
		)
		self.execute(q, list(conditions.values()))


	def get_rows_by_raw_conditions(self, table, conditions: list):
		"""
		Get rows from `table` where all raw SQL `conditions` are met.
		Each entry in `conditions` should be a valid SQL fragment, e.g.:
			["email = 'foo@example.com'", "is_active = true"]
		"""
		if not conditions:
			raise ValueError("Conditions list is empty.")

		where_clause = sql.SQL(" AND ").join(sql.SQL(cond) for cond in conditions)

		query = sql.SQL("SELECT * FROM {} WHERE {};").format(
			sql.Identifier(table),
			where_clause
		)

		return self.execute(query)

	def delete_rows_by_raw_conditions(self, table, conditions: list):
		"""Delete rows from `table` where all raw SQL `conditions` are met."""
		if not conditions:
			raise ValueError("Conditions list is empty.")

		where_clause = sql.SQL(" AND ").join(sql.SQL(cond) for cond in conditions)
		q = sql.SQL("DELETE FROM {table} WHERE {conds};").format(
			table=sql.Identifier(table),
			conds=where_clause
		)
		self.execute(q)

	def update_rows_by_conditions(self, table, updates: dict, conditions: dict,
								  lower_limit: int=None, upper_limit: int=None):
		"""
		Update rows in `table` setting columns from `updates` where each key-value in `conditions`
		is matched. Optionally enforce that the number of rows updated is within [lower_limit, upper_limit].
		"""
		if not updates:
			raise ValueError("Updates dictionary is empty.")
		if not conditions:
			raise ValueError("Conditions dictionary is empty.")

		# Validate table & columns
		valid_columns = self._get_table_columns(table)
		if not valid_columns:
			raise ValueError(f"Table '{table}' does not exist.")
		invalid_updates = [k for k in updates if k not in valid_columns]
		invalid_conds   = [k for k in conditions if k not in valid_columns]
		if invalid_updates:
			raise ValueError(f"Invalid columns to update: {invalid_updates}")
		if invalid_conds:
			raise ValueError(f"Invalid columns in conditions: {invalid_conds}")

		# Build SET and WHERE clauses
		set_clauses = [
			sql.SQL("{} = {}").format(sql.Identifier(col), sql.Placeholder())
			for col in updates.keys()
		]
		where_clauses = [
			sql.SQL("{} = {}").format(sql.Identifier(col), sql.Placeholder())
			for col in conditions.keys()
		]
		q = sql.SQL("UPDATE {table} SET {sets} WHERE {conds};").format(
			table=sql.Identifier(table),
			sets=sql.SQL(", ").join(set_clauses),
			conds=sql.SQL(" AND ").join(where_clauses)
		)
		params = list(updates.values()) + list(conditions.values())

		# manual execute so we can check rowcount
		conn = self._get_conn()
		try:
			with conn.cursor() as cur:
				cur.execute(q, params)
				count = cur.rowcount
				# enforce limits
				if lower_limit is not None and count < lower_limit:
					conn.rollback()
					raise ValueError(f"Expected ≥ {lower_limit} rows updated, got {count}.")
				if upper_limit is not None and count > upper_limit:
					conn.rollback()
					raise ValueError(f"Expected ≤ {upper_limit} rows updated, got {count}.")
				conn.commit()
		except:
			conn.rollback()
			raise
		finally:
			self._put_conn(conn)


	def update_rows_by_raw_conditions(self, table, updates: list, conditions: list,
									  lower_limit: int=None, upper_limit: int=None):
		"""
		Update rows in `table` using raw SQL assignment strings where all raw SQL `conditions`
		are met. Enforce affected-row bounds if given.
		"""
		if not updates:
			raise ValueError("Updates list is empty.")
		if not conditions:
			raise ValueError("Conditions list is empty.")

		set_clause   = sql.SQL(", ").join(sql.SQL(u) for u in updates)
		where_clause = sql.SQL(" AND ").join(sql.SQL(c) for c in conditions)
		q = sql.SQL("UPDATE {table} SET {sets} WHERE {conds};").format(
			table=sql.Identifier(table),
			sets=set_clause,
			conds=where_clause
		)

		conn = self._get_conn()
		try:
			with conn.cursor() as cur:
				cur.execute(q)
				count = cur.rowcount
				if lower_limit is not None and count < lower_limit:
					conn.rollback()
					raise ValueError(f"Expected ≥ {lower_limit} rows updated, got {count}.")
				if upper_limit is not None and count > upper_limit:
					conn.rollback()
					raise ValueError(f"Expected ≤ {upper_limit} rows updated, got {count}.")
				conn.commit()
		except:
			conn.rollback()
			raise
		finally:
			self._put_conn(conn)

	def apply_to_rows(self, table, conditions: dict, func, key_columns=['id']):
		"""
		Fetch rows matching `conditions`, call `func(row)` on each to get an updates-dict,
		then UPDATE each row, using `key_columns` to identify it.
		Returns the number of rows updated.
		"""
		# basic checks
		if not callable(func):
			raise ValueError("func must be callable")
		if not conditions:
			raise ValueError("Conditions dictionary is empty.")

		# fetch rows
		rows = self.get_rows_by_conditions(table, conditions)
		if not rows:
			return 0

		updated_count = 0
		valid_columns = self._get_table_columns(table)
		if not valid_columns:
			raise ValueError(f"Table '{table}' does not exist.")

		for row in rows:
			# call the user-provided function
			updates = func(row)
			if not isinstance(updates, dict):
				raise ValueError("func must return a dict of {column: new_value}")

			# validate update columns
			bad = [c for c in updates if c not in valid_columns]
			if bad:
				raise ValueError(f"Invalid update columns: {bad}")

			# build the key lookup for this row
			key_cond = {}
			for kc in key_columns:
				if kc not in row:
					raise ValueError(f"Key column '{kc}' not in the row data")
				key_cond[kc] = row[kc]

			# perform the update (uses your existing method)
			self.update_rows_by_conditions(table, updates, key_cond)
			updated_count += 1

		return updated_count

	def get_rows_by_predicates(
		self,
		table: str,
		predicates: list = None,     # [("col", ">=", val), ("col", "<", val), ("col","IS NULL", None)]
		columns: list = None,        # ["*", "col", ("count","*","alias"), ("count_distinct","col","alias"), ("avg","col","alias"), ...]
		order_by: list = None,       # [("col_or_alias","ASC"|"DESC")]
		limit: int = None,
		group_by: list = None,       # ["col", "col2"]  (identifiers only)
		schema: str = "public"
	):
		"""
		Select rows with flexible predicates and safe aggregate columns.

		`columns` entries may be:
		- "*" or "colname"  -> SELECT * / SELECT colname
		- ("count", "*", "alias") -> SELECT COUNT(*) AS alias
		- ("count_distinct", "col", "alias")
		- ("min"|"max"|"avg"|"sum", "col", "alias")

		`predicates` support: =, <>, !=, <, <=, >, >=, LIKE, ILIKE, IN, NOT IN, IS NULL, IS NOT NULL.
		For IS NULL / IS NOT NULL, pass value=None.
		"""
		valid_ops = {
			"=", "<>", "!=", "<", "<=", ">", ">=", "LIKE", "ILIKE",
			"IN", "NOT IN", "IS NULL", "IS NOT NULL"
		}
		agg_funcs = {"count", "count_distinct", "min", "max", "avg", "sum"}

		# Discover valid columns for identifier validation
		valid_columns = self._get_table_columns(table, schema)

		# SELECT list
		if columns is None:
			select_list = sql.SQL("*")
		else:
			#  allow "*" explicitly in the list
			if isinstance(columns, (list, tuple)) and len(columns) == 1 and columns[0] == "*":
				select_list = sql.SQL("*")
			else:
				select_bits = []
				for c in columns:
					# Plain identifier column
					if isinstance(c, str):
						if c not in valid_columns:
							raise ValueError(f"Unknown column in SELECT: {c}")
						select_bits.append(sql.Identifier(c))
						continue

					# Aggregate tuple
					if not (isinstance(c, (list, tuple)) and len(c) == 3):
						raise ValueError("Aggregate column spec must be a 3-tuple (func, col_or_*, alias)")
					func, col, alias = c
					func = str(func).lower()
					if func not in agg_funcs:
						raise ValueError(f"Unsupported aggregate: {func}")

					if func == "count" and col == "*":
						expr = sql.SQL("COUNT(*)")
					else:
						if col not in valid_columns:
							raise ValueError(f"Unknown column in aggregate: {col}")
						arg = sql.Identifier(col)
						if func == "count_distinct":
							expr = sql.SQL("COUNT(DISTINCT {})").format(arg)
						else:
							expr = sql.SQL("{}({})").format(sql.SQL(func.upper()), arg)

					expr = sql.SQL("{} AS {}").format(expr, sql.Identifier(alias))
					select_bits.append(expr)

				select_list = sql.SQL(", ").join(select_bits)

		# FROM
		q = sql.SQL("SELECT {cols} FROM {schema}.{tbl}").format(
			cols=select_list,
			schema=sql.Identifier(schema),
			tbl=sql.Identifier(table)
		)

		# WHERE
		params = []
		where_parts = []
		if predicates:
			for col, op, val in predicates:
				op_up = op.upper()
				if op_up not in valid_ops:
					raise ValueError(f"Unsupported operator: {op}")
				if col not in valid_columns:
					raise ValueError(f"Unknown column in predicate: {col}")

				if op_up in {"IN", "NOT IN"}:
					if not isinstance(val, (list, tuple, set)) or not val:
						raise ValueError(f"{op} requires a non-empty sequence")
					ph = sql.SQL(", ").join(sql.Placeholder() for _ in val)
					where_parts.append(sql.SQL("{} {} ({})").format(sql.Identifier(col), sql.SQL(op_up), ph))
					params.extend(list(val))
				elif op_up in {"IS NULL", "IS NOT NULL"}:  # 
					where_parts.append(sql.SQL("{} {}").format(sql.Identifier(col), sql.SQL(op_up)))
					# no param
				else:
					where_parts.append(sql.SQL("{} {} {}").format(sql.Identifier(col), sql.SQL(op_up), sql.Placeholder()))
					params.append(val)

		if where_parts:
			q += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where_parts)

		# GROUP BY (identifiers only)
		if group_by:
			for g in group_by:
				if g not in valid_columns:
					raise ValueError(f"Unknown column in GROUP BY: {g}")
			q += sql.SQL(" GROUP BY ") + sql.SQL(", ").join(sql.Identifier(g) for g in group_by)

		# ORDER BY (allow alias names or real columns)
		if order_by:
			order_bits = []
			for col, direction in order_by:
				dir_up = direction.upper()
				if dir_up not in {"ASC", "DESC"}:
					raise ValueError("order_by direction must be 'ASC' or 'DESC'")
				order_bits.append(sql.SQL("{} {}").format(sql.Identifier(col), sql.SQL(dir_up)))
			q += sql.SQL(" ORDER BY ") + sql.SQL(", ").join(order_bits)

		# LIMIT
		if limit is not None:
			if not isinstance(limit, int) or limit <= 0:
				raise ValueError("limit must be a positive integer")
			q += sql.SQL(" LIMIT {}").format(sql.Literal(limit))

		return self.execute(q, params)


	def get_min_max(self, table: str, column: str, schema: str = "public"):
		"""
		Return the minimum and maximum value of `column` in `table`.
		"""
		q = sql.SQL("SELECT MIN({col}) AS min_val, MAX({col}) AS max_val FROM {schema}.{tbl};").format(
			col=sql.Identifier(column),
			schema=sql.Identifier(schema),
			tbl=sql.Identifier(table)
		)
		result = self.execute(q)
		return result[0] if result else None

	def select_with_join(
		self,
		base_table: str,
		columns: list,
		joins: list = None,         # e.g. [("LEFT JOIN", "uptime_reports r", "r.report_date = u.epoch_date")]
		predicates: list = None,    # e.g. [("u.epoch_date", "<", today)]
		raw_predicates: list = None, # e.g. ["r.report_date IS NULL"]
		order_by: list = None,
		limit: int = None
	):
		"""
		Perform a SELECT with optional JOINs and predicates.
		`columns` is a list of SQL strings (with aliases if needed).
		`joins` is a list of (join_type, table_expr, on_clause).
		"""
		select_sql = sql.SQL(", ").join(sql.SQL(c) for c in columns)

		q = sql.SQL("SELECT {cols} FROM {base}").format(
			cols=select_sql,
			base=sql.SQL(base_table)
		)

		if joins:
			for join_type, table_expr, on_clause in joins:
				q += sql.SQL(" {} {} ON {}").format(
					sql.SQL(join_type),
					sql.SQL(table_expr),
					sql.SQL(on_clause)
				)

		clauses, params = [], []
		if predicates:
			for col, op, val in predicates:
				clauses.append(sql.SQL("{} {} {}").format(sql.SQL(col), sql.SQL(op), sql.Placeholder()))
				params.append(val)
		if raw_predicates:
			for raw in raw_predicates:
				clauses.append(sql.SQL(raw))
		if clauses:
			q += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses)

		if order_by:
			bits = [sql.SQL("{} {}").format(sql.SQL(c), sql.SQL(d)) for c, d in order_by]
			q += sql.SQL(" ORDER BY ") + sql.SQL(", ").join(bits)

		if limit:
			q += sql.SQL(" LIMIT {}").format(sql.Literal(limit))

		return self.execute(q, params)

	def count_rows(
		self,
		table: str,
		conditions: dict | None = None,           # supports {"col": v} and {"col >=": v}
		predicates: list[tuple] | None = None,     # e.g. [("epoch", ">=", 123), ("epoch", "<", 456)]
		distinct: str | None = None,
		schema: str = "public"
	) -> int:
		"""
		Return COUNT(*) (or COUNT(DISTINCT <col>)) from `table`, with optional filters.

		Filters can be provided either as:
		- conditions dict with equality or operator-in-key:
			{"col": val, "ts >=": 100, "ts <": 200, "id IN": [1,2,3]}
		- predicates list of (col, op, val) tuples:
			[("ts", ">=", 100), ("ts", "<", 200)]
		Supported ops: =, <>, !=, <, <=, >, >=, LIKE, ILIKE, IN, NOT IN
		"""
		valid_ops = {"=", "<>", "!=", "<", "<=", ">", ">=", "LIKE", "ILIKE", "IN", "NOT IN"}

		# COUNT(*) vs COUNT(DISTINCT col)
		if distinct:
			count_expr = sql.SQL("COUNT(DISTINCT {})").format(sql.Identifier(distinct))
		else:
			count_expr = sql.SQL("COUNT(*)")

		q = sql.SQL("SELECT {count} AS count FROM {schema}.{table}").format(
			count=count_expr,
			schema=sql.Identifier(schema),
			table=sql.Identifier(table)
		)

		params = []
		where_parts = []

		# Column validation
		valid_columns = self._get_table_columns(table, schema)
		def _ensure_col(c: str):
			if c not in valid_columns:
				raise ValueError(f"Unknown column: {c}")

		# Parse conditions dict (supports operator in key)
		if conditions:
			for key, val in conditions.items():
				key = str(key).strip()
				parts = key.split(None, 1)  # split on first whitespace
				if len(parts) == 1:
					col, op = parts[0], "="
				else:
					col, op = parts[0], parts[1].upper()
				_ensure_col(col)
				if op not in valid_ops:
					raise ValueError(f"Unsupported operator: {op}")
				if op in {"IN", "NOT IN"}:
					if not isinstance(val, (list, tuple, set)) or not val:
						raise ValueError(f"{op} requires a non-empty sequence")
					ph = sql.SQL(", ").join(sql.Placeholder() for _ in val)
					where_parts.append(sql.SQL("{} {} ({})").format(sql.Identifier(col), sql.SQL(op), ph))
					params.extend(list(val))
				else:
					where_parts.append(sql.SQL("{} {} {}").format(sql.Identifier(col), sql.SQL(op), sql.Placeholder()))
					params.append(val)

		# Parse predicates list
		if predicates:
			for col, op, val in predicates:
				col = str(col)
				op = str(op).upper()
				_ensure_col(col)
				if op not in valid_ops:
					raise ValueError(f"Unsupported operator: {op}")
				if op in {"IN", "NOT IN"}:
					if not isinstance(val, (list, tuple, set)) or not val:
						raise ValueError(f"{op} requires a non-empty sequence")
					ph = sql.SQL(", ").join(sql.Placeholder() for _ in val)
					where_parts.append(sql.SQL("{} {} ({})").format(sql.Identifier(col), sql.SQL(op), ph))
					params.extend(list(val))
				else:
					where_parts.append(sql.SQL("{} {} {}").format(sql.Identifier(col), sql.SQL(op), sql.Placeholder()))
					params.append(val)

		if where_parts:
			q += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where_parts)

		result = self.execute(q, params)
		return int(result[0]["count"]) if result else 0


	def insert_default_row(self, table: str, schema: str = "public"):
		"""
		Insert a row into `table` with all DEFAULT values.
		"""
		q = sql.SQL("INSERT INTO {schema}.{table} DEFAULT VALUES;").format(
			schema=sql.Identifier(schema),
			table=sql.Identifier(table)
		)
		self.execute(q)


if __name__ == "__main__":
	client = PSQLClient()
	print("Databases:", client.list_databases())
	print("Tables:", client.list_tables())
	client.close()
