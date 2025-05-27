import psycopg2
from psycopg2 import sql
from psycopg2.pool import ThreadedConnectionPool
from config_reader import ConfigReader

# Load database config
database_config = ConfigReader.get_key_value_config("database.config")

class PSQLClient:
	"""
	Thread-safe Postgres helper using a connection pool.
	"""
	def __init__(self, host=None, port=None, database=None, user=None, password=None, minconn=1, maxconn=10):
		# Configuration fallback
		self.host = host or database_config.get("HOST", "localhost")
		self.port = port or database_config.get("PORT", 5432)
		self.database = database or database_config.get("DATABASE", "postgres")
		self.user = user or database_config.get("USER", "postgres")
		self.password = password or database_config.get("PASSWORD", "")
		# Initialize a threaded connection pool
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

	def insert_row(self, table, data: dict):
		"""Insert a row into `table` using column-value mapping from `data`."""
		if not data:
			raise ValueError("Data dictionary is empty.")

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

if __name__ == "__main__":
	client = PSQLClient()
	print("Databases:", client.list_databases())
	print("Tables:", client.list_tables())
	client.close()
