import psycopg2
from util.config_reader import ConfigReader

# Read database config once at import time
db_config = ConfigReader.get_key_value_config("database.config")

def get_db_connection():
	try:
		conn = psycopg2.connect(
			host=db_config['host'],
			database=db_config['database'],
			user=db_config['user'],
			password=db_config['password']
		)
		return conn
	except Exception as e:
		print(f"[ERROR] Failed to connect to database: {e}")
		return None

def _debug_list_tables(cursor: psycopg2.extensions.cursor) -> None:
	cursor.execute("""
		SELECT tablename
		FROM pg_catalog.pg_tables
		WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
		ORDER BY tablename;
	""")
	tables = cursor.fetchall()

	if not tables:
		print("[DEBUG] No user tables found.")
	else:
		print(f"[DEBUG] Found {len(tables)} user tables:")
		for i, (tablename,) in enumerate(tables, 1):
			print(f"{i:>3}: {tablename}")

def _debug_dump_table(cursor: psycopg2.extensions.cursor, table_name: str, override: bool = False) -> None:
	debug_row_limit = 1000

	cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
	row_count = cursor.fetchone()[0]

	if row_count > debug_row_limit and not override:
		raise ValueError(
			f"[DEBUG] Row count ({row_count}) exceeds debug row limit ({debug_row_limit}). "
			f"Use override=True to dump all rows."
		)

	cursor.execute(f"SELECT * FROM {table_name};")
	columns = [desc[0] for desc in cursor.description]
	rows = cursor.fetchall()

	data = [dict(zip(columns, row)) for row in rows]
	print(f"[DEBUG] Dumping {len(data)} rows from '{table_name}':")
	for i, row in enumerate(data, 1):
		print(f"{i:>3}: {row}")
