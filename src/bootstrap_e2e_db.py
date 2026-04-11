from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

import psycopg2
from psycopg2 import sql


DEFAULT_DB_NAME = "websitedb_e2e"
DEFAULT_ROLE_NAME = "website_interface_e2e"
CONFIG_FILENAME = "website_db_e2e.conf"


def _load_kv_config(path: Path) -> dict[str, str]:
	config: dict[str, str] = {}
	if not path.exists():
		return config
	for raw_line in path.read_text(encoding="utf-8").splitlines():
		line = raw_line.strip()
		if not line or line.startswith("#") or "=" not in line:
			continue
		key, value = line.split("=", 1)
		config[key.strip()] = value.strip()
	return config


def _write_kv_config(path: Path, config: dict[str, str]) -> None:
	lines = [f"{key}={value}" for key, value in config.items() if value]
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _admin_connect():
	connect_kwargs = {
		"dbname": os.environ.get("BOOTSTRAP_PG_DATABASE", "postgres"),
	}
	for env_key, arg_name in (
		("BOOTSTRAP_PG_USER", "user"),
		("BOOTSTRAP_PG_PASSWORD", "password"),
		("BOOTSTRAP_PG_HOST", "host"),
		("BOOTSTRAP_PG_PORT", "port"),
	):
		value = (os.environ.get(env_key) or "").strip()
		if value:
			connect_kwargs[arg_name] = value
	return psycopg2.connect(**connect_kwargs)


def bootstrap_e2e_database() -> tuple[bool, str]:
	root = Path(__file__).resolve().parent
	config_path = root / "config" / CONFIG_FILENAME
	existing = _load_kv_config(config_path)

	db_name = (os.environ.get("WEBSITE_E2E_DB_NAME") or existing.get("database") or DEFAULT_DB_NAME).strip()
	role_name = (os.environ.get("WEBSITE_E2E_DB_USER") or existing.get("user") or DEFAULT_ROLE_NAME).strip()
	password = (os.environ.get("WEBSITE_E2E_DB_PASSWORD") or existing.get("password") or secrets.token_urlsafe(24)).strip()
	host = (os.environ.get("WEBSITE_E2E_DB_HOST") or existing.get("host") or "").strip()
	port = (os.environ.get("WEBSITE_E2E_DB_PORT") or existing.get("port") or "").strip()

	admin_conn = _admin_connect()
	admin_conn.autocommit = True
	try:
		with admin_conn.cursor() as cur:
			cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role_name])
			role_exists = cur.fetchone() is not None
			if role_exists:
				cur.execute(
					sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(role_name)),
					[password],
				)
			else:
				cur.execute(
					sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(role_name)),
					[password],
				)

			cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", [db_name])
			db_exists = cur.fetchone() is not None
			if not db_exists:
				cur.execute(
					sql.SQL("CREATE DATABASE {} OWNER {}").format(
						sql.Identifier(db_name),
						sql.Identifier(role_name),
					)
				)
			else:
				cur.execute(
					sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
						sql.Identifier(db_name),
						sql.Identifier(role_name),
					)
				)
	finally:
		admin_conn.close()

	target_kwargs = {
		"dbname": db_name,
	}
	if host:
		target_kwargs["host"] = host
	if port:
		target_kwargs["port"] = port
	target_conn = psycopg2.connect(**target_kwargs)
	target_conn.autocommit = True
	try:
		with target_conn.cursor() as cur:
			cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
			cur.execute(
				sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(
					sql.Identifier(db_name),
					sql.Identifier(role_name),
				)
			)
			cur.execute(
				sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(sql.Identifier(role_name))
			)
	finally:
		target_conn.close()

	verify_kwargs = {
		"dbname": db_name,
		"user": role_name,
		"password": password,
	}
	if host:
		verify_kwargs["host"] = host
	if port:
		verify_kwargs["port"] = port
	verify_conn = psycopg2.connect(**verify_kwargs)
	verify_conn.close()

	config = {
		"database": db_name,
		"user": role_name,
		"password": password,
	}
	if host:
		config["host"] = host
	if port:
		config["port"] = port
	_write_kv_config(config_path, config)

	return True, (
		f"Configured dedicated e2e database '{db_name}' and wrote local config to {config_path}."
	)


if __name__ == "__main__":
	ok, message = bootstrap_e2e_database()
	print(message)
	raise SystemExit(0 if ok else 1)
