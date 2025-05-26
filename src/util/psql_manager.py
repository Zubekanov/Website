import psycopg2
from psycopg2 import sql
from config_reader import ConfigReader

database_config = ConfigReader.get_key_value_config("database.config")

class PSQLClient:
	def __init__(self, host=None, port=None, database=None, user=None, password=None):
		self.database = database or database_config.get("DATABASE", "postgres")
		self.user = user or database_config.get("USER", "postgres")
		self.password = password or database_config.get("PASSWORD", "")
		self.conn = None

	def connect(self):
		"""Establish a connection if not already connected."""
		if not self.conn:
			self.conn = psycopg2.connect(
				database=self.database,
				user=self.user
			)
		return self.conn

	def close(self):
		"""Close the connection."""
		if self.conn:
			self.conn.close()
			self.conn = None

	def list_databases(self):
		"""Return a list of database names."""
		conn = self.connect()
		with conn.cursor() as cur:
			cur.execute(
				"SELECT datname FROM pg_database WHERE datistemplate = false;"
			)
			return [row[0] for row in cur.fetchall()]

	def list_tables(self, schema="public"):
		"""Return a list of table names in the given schema."""
		conn = self.connect()
		with conn.cursor() as cur:
			cur.execute(
				sql.SQL(
					"SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = %s;"
				), [schema]
			)
			return [row[0] for row in cur.fetchall()]

	def get_records(self, table, limit=100):
		"""Fetch up to `limit` records from `table`."""
		conn = self.connect()
		with conn.cursor() as cur:
			query = sql.SQL(
				"SELECT * FROM {table} LIMIT %s;"
			).format(
				table=sql.Identifier(table)
			)
			cur.execute(query, [limit])
			colnames = [desc[0] for desc in cur.description]
			rows = cur.fetchall()
			return [dict(zip(colnames, row)) for row in rows]

	def execute(self, query, params=None):
		"""Execute arbitrary SQL. Returns rows if SELECT, else commits."""
		conn = self.connect()
		with conn.cursor() as cur:
			cur.execute(query, params or [])
			if cur.description:
				colnames = [desc[0] for desc in cur.description]
				rows = cur.fetchall()
				return [dict(zip(colnames, row)) for row in rows]
			else:
				conn.commit()
				return None

	def drop_table(self, table, schema="public"):
		"""Drop table if it exists."""
		conn = self.connect()
		with conn.cursor() as cur:
			cur.execute(
				sql.SQL("DROP TABLE IF EXISTS {schema}.{table} CASCADE;")
				.format(
					schema=sql.Identifier(schema),
					table=sql.Identifier(table)
				)
			)
			conn.commit()

	def create_database(self, db_name):
		"""Create a new database."""
		conn = self.connect()
		with conn.cursor() as cur:
			cur.execute(
				sql.SQL("CREATE DATABASE {db_name};").format(
					db_name=sql.Identifier(db_name)
				)
			)
			conn.commit()

	def drop_database(self, db_name):
		"""Drop a database if it exists."""
		conn = self.connect()
		with conn.cursor() as cur:
			cur.execute(
				sql.SQL("DROP DATABASE IF EXISTS {db_name};").format(
					db_name=sql.Identifier(db_name)
				)
			)
			conn.commit()

if __name__ == "__main__":
	client = PSQLClient()
	print("Databases:", client.list_databases())
	print("Tables:", client.list_tables())
	client.close()
