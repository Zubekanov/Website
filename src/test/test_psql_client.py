# test/test_psql_client.py
# Black-box tests for sql.psql_client.PSQLClient based on the behaviour brief.
# Tabs used for indentation per project preference.

import os
import math
import time
import uuid
import pytest

from sql.psql_client import PSQLClient


# --------------------------
# Helpers / fixtures
# --------------------------

def _conn_kwargs_from_env():
	"""
	Read connection details from standard PG* env vars with safe defaults.
	Tests operate inside the connected database (schemas/tables only).
	"""
	return {
		"database": os.getenv("PGDATABASE", "postgres"),
		"user": os.getenv("PGUSER", "postgres"),
		"password": os.getenv("PGPASSWORD"),            # may be None for local trust
		"host": os.getenv("PGHOST", "localhost"),
		"port": int(os.getenv("PGPORT", "5432")),
	}


@pytest.fixture(scope="session")
def client():
	"""
	Session-scoped pooled client (per-DSN cache).
	Initialised dynamically from environment, no config files.
	"""
	kwargs = _conn_kwargs_from_env()
	c = PSQLClient.get(**kwargs)
	yield c
	# Ensure pools are closed after the test session
	PSQLClient.closeall()


@pytest.fixture
def schema_name():
	"""
	Unique, isolated schema per test. Ensures cleanup.
	"""
	return f"t_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def ensure_clean_schema(client, schema_name):
	"""
	Create a fresh schema and drop it after the test.
	"""
	client.create_schema(schema_name, exists_ok=True)
	yield schema_name
	client.drop_schema(schema_name, cascade=True, missing_ok=True)


def fq(schema, table):
	return f'{schema}.{table}'


# --------------------------
# 1) Client cache (per-DSN)
# --------------------------

def test_client_cache_identity():
	kwargs = _conn_kwargs_from_env()

	a = PSQLClient.get(**kwargs)
	b = PSQLClient.get(**kwargs)
	assert a is b, "Expected PSQLClient.get(...) to return the same instance for identical params"

	# Different DSN (e.g., min/max pool sizes differ) → different instance
	c = PSQLClient.get(**kwargs, minconn=2, maxconn=11)
	assert c is not a, "Different pool sizing should yield a distinct cached client"

	# Direct construction is allowed but is not cached (by design)
	d = PSQLClient(**kwargs)
	assert d is not a, "Direct __init__ does not consult the cache"
	d.close()


# --------------------------
# 2) Schema / table lifecycle & introspection
# --------------------------

def test_schema_lifecycle(client, schema_name):
	assert not client.schema_exists(schema_name)
	client.create_schema(schema_name)
	assert client.schema_exists(schema_name)
	schemas = client.list_schemas()
	assert schema_name in schemas
	# Drop and verify gone
	client.drop_schema(schema_name, cascade=True)
	assert not client.schema_exists(schema_name)


def test_ensure_schema_idempotent(client, schema_name):
	client.ensure_schema(schema_name)
	client.ensure_schema(schema_name)  # should not error
	assert client.schema_exists(schema_name)


def test_table_lifecycle_and_columns(client, ensure_clean_schema):
	schema = ensure_clean_schema
	table = "people"
	cols = {"id": "SERIAL PRIMARY KEY", "name": "TEXT", "age": "INT"}
	client.create_table(schema, table, cols, if_not_exists=True)
	assert client.table_exists(schema, table)
	assert "people" in client.list_tables(schema=schema)

	# Column checks
	table_cols = client.get_table_columns(schema, table)
	assert set(table_cols) >= {"id", "name", "age"}
	assert client.column_exists(schema, table, "age")
	assert not client.column_exists(schema, table, "nope")

	# Idempotent create
	client.ensure_table(schema, table, cols)

	# Drop
	client.drop_table(schema, table, cascade=True)
	assert not client.table_exists(schema, table)


def test_create_table_validates_non_empty_columns(client, ensure_clean_schema):
	with pytest.raises(ValueError):
		client.create_table(ensure_clean_schema, "empty_cols", {})


# --------------------------
# 3) Index helpers
# --------------------------

def test_index_helpers(client, ensure_clean_schema):
	schema = ensure_clean_schema
	table = "events"
	client.create_table(schema, table, {"id": "SERIAL PRIMARY KEY", "ts": "TIMESTAMPTZ", "kind": "TEXT"})
	index_name = f"{table}_ts_idx"
	assert not client.index_exists(schema, index_name)
	client.create_index(schema, table, index_name, ["ts"], unique=False, if_not_exists=True)
	assert client.index_exists(schema, index_name)
	client.drop_index(schema, index_name, missing_ok=False)
	assert not client.index_exists(schema, index_name)


def test_create_index_validates_non_empty_columns(client, ensure_clean_schema):
	schema = ensure_clean_schema
	table = "t"
	client.create_table(schema, table, {"id": "SERIAL PRIMARY KEY"})
	with pytest.raises(ValueError):
		client.create_index(schema, table, "t_idx", [])


# --------------------------
# 4) Row helpers (CRUD) + joins
# --------------------------

@pytest.fixture
def people_org_setup(client, ensure_clean_schema):
	schema = ensure_clean_schema
	client.create_table(schema, "orgs", {"id": "SERIAL PRIMARY KEY", "name": "TEXT NOT NULL"})
	client.create_table(
		schema, "people",
		{"id": "SERIAL PRIMARY KEY", "name": "TEXT NOT NULL", "age": "INT", "org_id": "INT"},
		constraints=[f"FOREIGN KEY (org_id) REFERENCES {fq(schema,'orgs')}(id)"]
	)
	yield schema
	# cleanup handled by schema teardown


def test_insert_and_returning(client, people_org_setup):
	schema = people_org_setup
	org_row = client.insert_row(fq(schema, "orgs"), {"name": "Acme"})
	assert isinstance(org_row, dict)
	assert org_row["name"] == "Acme"

	emp = client.insert_row(fq(schema, "people"), {"name": "Eve", "age": 31, "org_id": org_row["id"]})
	assert emp["name"] == "Eve" and emp["org_id"] == org_row["id"]


def test_insert_empty_data_raises(client, people_org_setup):
	with pytest.raises(ValueError):
		client.insert_row(f"{people_org_setup}.people", {})


def test_select_equalities_raw_joins_pagination_and_ordering(client, people_org_setup):
	schema = people_org_setup
	# Seed orgs
	acme = client.insert_row(fq(schema, "orgs"), {"name": "Acme"})
	beta = client.insert_row(fq(schema, "orgs"), {"name": "Beta"})
	# Seed people
	for nm, age, org in [
		("Ann", 30, acme["id"]),
		("Bob", 30, acme["id"]),
		("Cid", 40, acme["id"]),
		("Di", 25, beta["id"]),
		("Eli", 28, beta["id"]),
		("Flo", 30, beta["id"]),
	]:
		client.insert_row(fq(schema, "people"), {"name": nm, "age": age, "org_id": org})

	# Base table validations: invalid equality key should fail
	with pytest.raises(ValueError):
		client.get_rows_with_filters(
			fq(schema, "people"),
			equalities={"nonexistent": 1},
		)

	# Join to orgs to filter by org name, but order by base table column (age desc, id tiebreaker)
	joins = [f"JOIN {fq(schema,'orgs')} o ON o.id = people.org_id"]
	rows, total_pages = client.get_rows_with_filters(
		fq(schema, "people"),
		equalities={"age": 30},
		raw_conditions="o.name IN (%s, %s)",
		raw_params=("Acme", "Beta"),
		joins=joins,
		page_limit=2,
		page_num=0,
		order_by="age",
		order_dir="DESC",
	)
	assert total_pages >= 2
	assert len(rows) <= 2
	assert all(r["age"] == 30 for r in rows)

	# Page through all matching rows deterministically
	seen = []
	page = 0
	while True:
		page_rows, total_pages = client.get_rows_with_filters(
			fq(schema, "people"),
			equalities={"age": 30},
			joins=joins,
			page_limit=2,
			page_num=page,
			order_by="id",  # deterministic
			order_dir="ASC",
		)
		seen.extend(page_rows)
		if page + 1 >= total_pages:
			break
		page += 1

	# Verify combined results contain all age==30
	all_thirty = [r for r in seen if r["age"] == 30]
	assert len(all_thirty) >= 3  # Ann/Bob and Flo
	# Page beyond last → empty with total_pages stable
	out_of_range, tp2 = client.get_rows_with_filters(
		fq(schema, "people"),
		equalities={"age": 30},
		joins=joins,
		page_limit=2,
		page_num=999,
		order_by="id",
		order_dir="ASC",
	)
	assert out_of_range == [] and tp2 == total_pages

	# Empty result → ([], 0)
	none_rows, tp0 = client.get_rows_with_filters(
		fq(schema, "people"),
		equalities={"age": 12345},
		page_limit=3,
		page_num=0,
	)
	assert none_rows == [] and tp0 == 0


def test_get_rows_validates_paging(client, people_org_setup):
	schema = people_org_setup
	with pytest.raises(ValueError):
		client.get_rows_with_filters(fq(schema, "people"), page_limit=0)
	with pytest.raises(ValueError):
		client.get_rows_with_filters(fq(schema, "people"), page_limit=10, page_num=-1)


def test_delete_rows_with_filters_and_safety(client, people_org_setup):
	schema = people_org_setup
	client.insert_row(fq(schema, "orgs"), {"name": "DelOrg"})
	r1 = client.insert_row(fq(schema, "people"), {"name": "X", "age": 1, "org_id": None})
	r2 = client.insert_row(fq(schema, "people"), {"name": "Y", "age": 1, "org_id": None})
	r3 = client.insert_row(fq(schema, "people"), {"name": "Z", "age": 2, "org_id": None})

	# Safety: no predicates
	with pytest.raises(ValueError):
		client.delete_rows_with_filters(fq(schema, "people"))

	# Delete by equality
	deleted = client.delete_rows_with_filters(fq(schema, "people"), equalities={"age": 1})
	assert deleted == 2

	# Nothing left with age=1
	left, _ = client.get_rows_with_filters(fq(schema, "people"), equalities={"age": 1})
	assert left == []


def test_update_rows_variants_and_safety(client, people_org_setup):
	schema = people_org_setup
	p = client.insert_row(fq(schema, "people"), {"name": "Old", "age": 10, "org_id": None})

	# update_rows_with_equalities validates non-empty dicts & columns
	with pytest.raises(ValueError):
		client.update_rows_with_equalities(fq(schema, "people"), updates={}, equalities={"id": p["id"]})
	with pytest.raises(ValueError):
		client.update_rows_with_equalities(fq(schema, "people"), updates={"age": 11}, equalities={})
	with pytest.raises(ValueError):
		client.update_rows_with_equalities(fq(schema, "people"), updates={"nope": 1}, equalities={"id": p["id"]})

	changed = client.update_rows_with_equalities(
		fq(schema, "people"),
		updates={"age": 11},
		equalities={"id": p["id"]},
	)
	assert changed == 1
	row, _ = client.get_rows_with_filters(fq(schema, "people"), equalities={"id": p["id"]})
	assert row and row[0]["age"] == 11

	# update_rows_with_filters safety: requires WHERE
	with pytest.raises(ValueError):
		client.update_rows_with_filters(fq(schema, "people"), updates={"age": 12})

	# Valid filters (raw)
	changed2 = client.update_rows_with_filters(
		fq(schema, "people"),
		updates={"age": 12},
		raw_conditions="id = %s",
		raw_params=(p["id"],),
	)
	assert changed2 == 1
	row2, _ = client.get_rows_with_filters(fq(schema, "people"), equalities={"id": p["id"]})
	assert row2 and row2[0]["age"] == 12


# --------------------------
# 5) Pagination helper (internal but tested)
# --------------------------

def test_paged_execute_happy_and_rejects_non_select(client, people_org_setup):
	schema = people_org_setup
	client.insert_row(fq(schema, "people"), {"name": "A", "age": 1, "org_id": None})
	client.insert_row(fq(schema, "people"), {"name": "B", "age": 1, "org_id": None})
	client.insert_row(fq(schema, "people"), {"name": "C", "age": 2, "org_id": None})

	# Accepts SELECT and WITH, appends ORDER/LIMIT/OFFSET
	rows = client._paged_execute(
		f"SELECT * FROM {fq(schema,'people')} WHERE age = %s",
		params=(1,),
		page_limit=1,
		page_num=1,
		order_by="id",
		order_dir="ASC",
		tiebreaker="id",
	)
	assert len(rows) == 1

	with pytest.raises(ValueError):
		client._paged_execute("DELETE FROM whatever")


def test_paged_execute_validates_paging(client):
	with pytest.raises(ValueError):
		client._paged_execute("SELECT 1", page_limit=0)
	with pytest.raises(ValueError):
		client._paged_execute("SELECT 1", page_limit=10, page_num=-1)


# --------------------------
# 6) Introspection helper (table-only)
# --------------------------

def test_get_table_columns_table_only(client, ensure_clean_schema):
	# Non-existent table → empty list
	assert client._get_table_columns("does_not_exist") == []
	# Create a simple table and query via table-only helper
	client.create_table(ensure_clean_schema, "t", {"id": "SERIAL PRIMARY KEY", "v": "INT"})
	cols = client._get_table_columns("t")
	assert "id" in cols and "v" in cols


# --------------------------
# 7) Error paths & rollback
# --------------------------

def test_invalid_column_names_raise_and_rollback(client, ensure_clean_schema):
	schema = ensure_clean_schema
	client.create_table(schema, "t", {"id": "SERIAL PRIMARY KEY", "v": "INT"})
	# Bad insert should raise and leave table usable
	with pytest.raises(Exception):
		client.insert_row(fq(schema, "t"), {"nope": 1})  # column doesn't exist
	# Subsequent valid insert still works → implies rollback occurred
	ok = client.insert_row(fq(schema, "t"), {"v": 2})
	assert ok["v"] == 2
