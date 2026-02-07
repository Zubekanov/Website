from __future__ import annotations

import argparse

from sql.psql_client import PSQLClient
from util.fcr.file_config_reader import FileConfigReader


def _load_db_config(config_name: str) -> dict[str, str]:
	fcr = FileConfigReader()
	conf = fcr.find(config_name)
	if not isinstance(conf, dict):
		raise RuntimeError(f"Config '{config_name}' is invalid.")
	return conf


def _build_client(conf: dict[str, str]) -> PSQLClient:
	database = (conf.get("database") or "").strip()
	user = (conf.get("user") or "").strip()
	password = conf.get("password")
	host = (conf.get("host") or "").strip() or None
	port_raw = (conf.get("port") or "").strip()
	port = int(port_raw) if port_raw else None

	if not database:
		raise RuntimeError("Missing 'database' in DB config.")
	if not user:
		raise RuntimeError("Missing 'user' in DB config.")
	if not password:
		raise RuntimeError("Missing 'password' in DB config.")

	return PSQLClient(
		database=database,
		user=user,
		password=password,
		host=host,
		port=port,
	)


def clear_database(*, config_name: str, schema: str = "public") -> tuple[bool, str]:
	conf = _load_db_config(config_name)
	client = _build_client(conf)
	try:
		rows = client.execute_query(
			"""
			SELECT tablename
			FROM pg_tables
			WHERE schemaname = %s
			ORDER BY tablename;
			""",
			[schema],
		) or []
		table_names = [r["tablename"] for r in rows if r.get("tablename")]

		if not table_names:
			return True, f"No tables found in schema '{schema}'."

		table_refs = ", ".join(f'"{schema}"."{name}"' for name in table_names)
		client.execute_query(f"TRUNCATE TABLE {table_refs} RESTART IDENTITY CASCADE;")
		return True, f"Cleared {len(table_names)} table(s) in schema '{schema}'."
	finally:
		client.close()


def main() -> int:
	parser = argparse.ArgumentParser(
		description="Clear all rows from the configured PostgreSQL database schema.",
	)
	parser.add_argument(
		"--config",
		default="website_db.conf",
		help="Config file name under src/config (default: website_db.conf).",
	)
	parser.add_argument(
		"--schema",
		default="public",
		help="Schema to clear (default: public).",
	)
	parser.add_argument(
		"--yes",
		action="store_true",
		help="Required safety flag to perform the destructive action.",
	)
	args = parser.parse_args()

	if not args.yes:
		print("Refusing to run without --yes.")
		return 2

	try:
		conf = _load_db_config(args.config)
		target_db = conf.get("database", "<unknown>")
		target_user = conf.get("user", "<unknown>")
		print(f"Target database: {target_db}")
		print(f"Target user: {target_user}")
		ok, message = clear_database(config_name=args.config, schema=args.schema)
		print(message)
		return 0 if ok else 1
	except Exception as exc:
		print(f"Failed to clear database: {exc}")
		return 1


if __name__ == "__main__":
	raise SystemExit(main())
