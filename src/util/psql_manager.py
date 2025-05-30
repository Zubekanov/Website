from psycopg2 import sql
from psycopg2.pool import ThreadedConnectionPool
from util.config_reader import ConfigReader

# Load database config
database_config = ConfigReader.get_key_value_config("database.config")

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

	def insert_row(self, table, data: dict):
		"""Insert a row into `table` using column-value mapping from `data`."""
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

		q = sql.SQL("INSERT INTO {table} ({fields}) VALUES ({placeholders});").format(
			table=sql.Identifier(table),
			fields=sql.SQL(', ').join(columns),
			placeholders=sql.SQL(', ').join(placeholders)
		)
		self.execute(q, values)

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
		"""Get rows from `table` where all raw SQL `conditions` are met."""
		if not conditions:
			raise ValueError("Conditions list is empty.")

		where_clause = sql.SQL(" AND ").join(sql.SQL(cond) for cond in conditions)
		q = sql.SQL("SELECT * FROM {table} WHERE {conds};").format(
			table=sql.Identifier(table),
			conds=where_clause
		)
		return self.execute(q)

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


if __name__ == "__main__":
	client = PSQLClient()
	print("Databases:", client.list_databases())
	print("Tables:", client.list_tables())
	client.close()
