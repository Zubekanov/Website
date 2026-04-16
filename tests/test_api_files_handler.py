from __future__ import annotations

import io
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import flask
import pytest

import app.api_handlers.files as files_mod

# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

MEMBER = {"id": "user-1", "email": "m@example.com"}
ADMIN  = {"id": "admin-1", "email": "a@example.com"}
FILE_ID   = "file-001"
FOLDER_ID = "folder-001"
LINK_ID   = "link-001"


class _FilesClient:
    """Stub DB client for file handler tests.

    rows_map: {table: list[dict]}  — rows returned by get_rows_with_filters.
    If a table's value is a list-of-lists, each sub-list is consumed one call at a time.
    """

    def __init__(self, *, rows_map=None, raise_on=None):
        self.rows_map: dict = rows_map or {}
        self.raise_on: set = raise_on or set()
        self.calls: list = []
        self._call_counts: dict[str, int] = {}

    def get_rows_with_filters(self, table, equalities=None, **kwargs):
        self.calls.append(("get", table, equalities or {}))
        rows = self.rows_map.get(table, [])
        if rows and isinstance(rows[0], list):          # side-effect queue
            idx = self._call_counts.get(table, 0)
            result = rows[idx] if idx < len(rows) else []
            self._call_counts[table] = idx + 1
        else:
            result = rows
        return result, len(result)

    def insert_row(self, table, data):
        if "insert_row" in self.raise_on:
            raise RuntimeError("db error")
        row = {"id": str(uuid.uuid4()), **data}
        self.calls.append(("insert", table, dict(data)))
        return row

    def update_rows_with_filters(self, table, fields, **kwargs):
        if "update" in self.raise_on:
            raise RuntimeError("db error")
        self.calls.append(("update", table, dict(fields)))

    def delete_rows_with_filters(self, table, **kwargs):
        self.calls.append(("delete", table, {}))


class _FilesInterface:
    def __init__(self, client, *, is_admin_ids=None, query_rows=None):
        self.client = client
        self._admin_ids: set = set(is_admin_ids or [])
        self._query_rows: list = query_rows or []

    def is_admin(self, user_id):
        return user_id in self._admin_ids

    def execute_query(self, sql, params=None):
        return self._query_rows


def _make_ctx(client=None, *, is_admin_ids=None, query_rows=None):
    c = client or _FilesClient()
    return SimpleNamespace(
        auth_token_name="session",
        interface=_FilesInterface(c, is_admin_ids=is_admin_ids, query_rows=query_rows),
        fcr=SimpleNamespace(find=lambda _: None),
    )


def _make_app(app_factory, ctx, tmp_path):
    app = app_factory(files_mod.register, ctx)
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    return app


def _quota_row(status="approved", quota_bytes=10 * 1024 * 1024, used_bytes=0):
    return {
        "user_id": MEMBER["id"],
        "quota_bytes": quota_bytes,
        "used_bytes": used_bytes,
        "status": status,
        "request_note": "test",
        "admin_note": None,
        "requested_at": "2024-01-01T00:00:00+00:00",
        "approved_at": "2024-01-02T00:00:00+00:00",
    }


def _file_row(user_id=None, stored_name=None, mime_type="text/plain"):
    return {
        "id": FILE_ID,
        "user_id": user_id or MEMBER["id"],
        "original_name": "test.txt",
        "stored_name": stored_name or uuid.uuid4().hex,
        "mime_type": mime_type,
        "size_bytes": 42,
        "download_count": 0,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }


def _folder_row(user_id=None, file_count=0):
    return {
        "id": FOLDER_ID,
        "user_id": user_id or MEMBER["id"],
        "name": "My Folder",
        "file_count": file_count,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }


def _link_row(created_by=None):
    return {
        "id": LINK_ID,
        "created_by": created_by or MEMBER["id"],
        "target_type": "file",
        "file_id": FILE_ID,
        "folder_id": None,
        "is_enabled": True,
        "download_count": 0,
        "file_name": "test.txt",
        "file_size": 42,
        "folder_name": None,
        "created_at": "2024-01-01T00:00:00+00:00",
        "last_accessed_at": None,
    }


# ---------------------------------------------------------------------------
# 1. Pure helper unit tests — no Flask context required
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mime_type,expected", [
    (None,                         "application/octet-stream"),
    ("",                           "application/octet-stream"),
    ("text/html",                  "application/octet-stream"),
    ("text/html; charset=utf-8",   "application/octet-stream"),
    ("application/javascript",     "application/octet-stream"),
    ("text/javascript",            "application/octet-stream"),
    ("application/x-sh",          "application/octet-stream"),
    ("application/x-httpd-php",   "application/octet-stream"),
    ("application/x-executable",  "application/octet-stream"),
    ("text/plain",                 "text/plain"),
    ("text/plain; charset=utf-8",  "text/plain; charset=utf-8"),
    ("application/pdf",            "application/pdf"),
    ("image/png",                  "image/png"),
    ("video/mp4",                  "video/mp4"),
])
def test_safe_mime_type(mime_type, expected):
    assert files_mod._safe_mime_type(mime_type) == expected


@pytest.mark.parametrize("n,expected", [
    (0,                  "0.0 B"),
    (512,                "512.0 B"),
    (1024,               "1.0 KB"),
    (2 * 1024,           "2.0 KB"),
    (2 * 1024 ** 2,      "2.0 MB"),
    (3 * 1024 ** 3,      "3.0 GB"),
    (5 * 1024 ** 4,      "5.0 TB"),
])
def test_fmt_bytes(n, expected):
    assert files_mod._fmt_bytes(n) == expected


def test_iso_none():
    assert files_mod._iso(None) is None


def test_iso_string_passthrough():
    assert files_mod._iso("2024-01-01") == "2024-01-01"


def test_iso_datetime():
    dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    result = files_mod._iso(dt)
    assert "2024-06-15" in result


def test_iso_other_type():
    assert files_mod._iso(42) == "42"


# ---------------------------------------------------------------------------
# 2. _safe_dest — requires Flask app context (UPLOAD_FOLDER)
# ---------------------------------------------------------------------------

def test_safe_dest_valid(app_factory, tmp_path):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    with app.app_context():
        path = files_mod._safe_dest("user-1", "abcdef1234")
        assert path.startswith(str(tmp_path))
        assert "user-1" in path
        assert "abcdef1234" in path


def test_safe_dest_traversal_raises(app_factory, tmp_path):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    with app.app_context():
        with pytest.raises(ValueError, match="Path traversal"):
            files_mod._safe_dest("user-1", "../../../etc/passwd")


def test_safe_dest_user_id_traversal_raises(app_factory, tmp_path):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    with app.app_context():
        with pytest.raises(ValueError, match="Path traversal"):
            files_mod._safe_dest("../other", "stored")


# ---------------------------------------------------------------------------
# 3. GET /api/files/quota
# ---------------------------------------------------------------------------

def test_quota_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = app.test_client().get("/api/files/quota")
    assert resp.status_code == 401


def test_quota_admin_user(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx(is_admin_ids={"admin-1"}, query_rows=[{"total": 500}])
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: ADMIN)
    resp = app.test_client().get("/api/files/quota")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["is_admin"] is True
    assert body["quota_bytes"] == files_mod.ADMIN_DISPLAY_QUOTA


def test_quota_member_has_record(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"user_storage_quotas": [_quota_row()]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().get("/api/files/quota")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "approved"
    assert body["quota_bytes"] == 10 * 1024 * 1024


def test_quota_member_no_record(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().get("/api/files/quota")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "none"


# ---------------------------------------------------------------------------
# 4. POST /api/files/quota/request
# ---------------------------------------------------------------------------

def test_quota_request_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = app.test_client().post("/api/files/quota/request", json={"quota_bytes": 1000, "note": "need space"})
    assert resp.status_code == 401


def test_quota_request_admin_rejected(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx(is_admin_ids={"admin-1"})
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: ADMIN)
    resp = app.test_client().post("/api/files/quota/request", json={"quota_bytes": 1000, "note": "need"})
    assert resp.status_code == 400
    assert "unlimited" in resp.get_json()["message"].lower()


@pytest.mark.parametrize("payload", [
    {"quota_bytes": "not-an-int", "note": "need"},
    {"quota_bytes": 0, "note": "need"},
    {"quota_bytes": -1, "note": "need"},
    {"note": "need"},
])
def test_quota_request_invalid_quota_bytes(app_factory, tmp_path, monkeypatch, payload):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post("/api/files/quota/request", json=payload)
    assert resp.status_code == 400
    assert "Invalid quota size" in resp.get_json()["message"]


def test_quota_request_missing_note(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post("/api/files/quota/request", json={"quota_bytes": 1000, "note": "  "})
    assert resp.status_code == 400
    assert "reason" in resp.get_json()["message"].lower()


def test_quota_request_exceeds_cap(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    over_50gb = 51 * 1024 * 1024 * 1024
    resp = app.test_client().post("/api/files/quota/request", json={"quota_bytes": over_50gb, "note": "need"})
    assert resp.status_code == 400
    assert "50 GB" in resp.get_json()["message"]


def test_quota_request_already_approved(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"user_storage_quotas": [_quota_row(status="approved")]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post("/api/files/quota/request", json={"quota_bytes": 1000, "note": "need"})
    assert resp.status_code == 400
    assert "active quota" in resp.get_json()["message"].lower()


def test_quota_request_re_request_updates_existing(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"user_storage_quotas": [_quota_row(status="denied")]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post("/api/files/quota/request", json={"quota_bytes": 1000, "note": "trying again"})
    assert resp.status_code == 200
    update_calls = [(op, t, f) for op, t, f in client.calls if op == "update" and t == "user_storage_quotas"]
    assert len(update_calls) == 1
    assert update_calls[0][2]["status"] == "pending"


def test_quota_request_new_inserts_row(app_factory, tmp_path, monkeypatch):
    client = _FilesClient()
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post("/api/files/quota/request", json={"quota_bytes": 1000, "note": "first time"})
    assert resp.status_code == 200
    insert_calls = [(op, t) for op, t, *_ in client.calls if op == "insert" and t == "user_storage_quotas"]
    assert len(insert_calls) == 1


# ---------------------------------------------------------------------------
# 5. GET /api/files/list
# ---------------------------------------------------------------------------

def test_files_list_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = app.test_client().get("/api/files/list")
    assert resp.status_code == 401


def test_files_list_returns_file_entries(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx(_FilesClient(rows_map={"user_files": [_file_row()]}))
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().get("/api/files/list")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert len(body["files"]) == 1
    assert body["files"][0]["original_name"] == "test.txt"


# ---------------------------------------------------------------------------
# 6. POST /api/files/upload
# ---------------------------------------------------------------------------

def _upload(client, data_bytes=b"hello", filename="test.txt", extra_headers=None):
    headers = {"X-Filename": filename}
    if extra_headers:
        headers.update(extra_headers)
    return client.post(
        "/api/files/upload",
        data=data_bytes,
        content_type="application/octet-stream",
        headers=headers,
    )


def test_upload_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = _upload(app.test_client())
    assert resp.status_code == 401


def test_upload_no_approved_quota(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = _upload(app.test_client())
    assert resp.status_code == 403


def test_upload_pending_quota_rejected(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"user_storage_quotas": [_quota_row(status="pending")]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = _upload(app.test_client())
    assert resp.status_code == 403


def test_upload_quota_full(app_factory, tmp_path, monkeypatch):
    q = _quota_row(quota_bytes=10, used_bytes=10)
    client = _FilesClient(rows_map={"user_storage_quotas": [q]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = _upload(app.test_client())
    assert resp.status_code == 400
    assert "quota full" in resp.get_json()["message"].lower()


def test_upload_no_filename_header(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"user_storage_quotas": [_quota_row()]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post("/api/files/upload", data=b"data", content_type="application/octet-stream")
    assert resp.status_code == 400
    assert "Filename" in resp.get_json()["message"]


def test_upload_empty_filename(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"user_storage_quotas": [_quota_row()]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = _upload(app.test_client(), data_bytes=b"data", filename="")
    assert resp.status_code == 400
    assert "Filename" in resp.get_json()["message"]


def test_upload_empty_file_rejected(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"user_storage_quotas": [_quota_row()]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = _upload(app.test_client(), data_bytes=b"")
    assert resp.status_code == 400
    assert "Empty" in resp.get_json()["message"]
    # No file should be left on disk
    assert list(tmp_path.rglob("*")) == [] or all(p.is_dir() for p in tmp_path.rglob("*"))


def test_upload_file_exceeds_quota_mid_stream(app_factory, tmp_path, monkeypatch):
    q = _quota_row(quota_bytes=5, used_bytes=0)
    client = _FilesClient(rows_map={"user_storage_quotas": [q]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = _upload(app.test_client(), data_bytes=b"hello world!")  # 12 bytes > 5
    assert resp.status_code == 400
    assert "too large" in resp.get_json()["message"].lower()
    # No file should be left on disk
    disk_files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert disk_files == []


def test_upload_dangerous_mime_type_stored_as_octet_stream(app_factory, tmp_path, monkeypatch):
    q = _quota_row(quota_bytes=100 * 1024)
    client = _FilesClient(rows_map={"user_storage_quotas": [q]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = _upload(app.test_client(), data_bytes=b"<html></html>", filename="evil.html")
    assert resp.status_code == 200
    insert_calls = [(op, t, d) for op, t, d in client.calls if op == "insert" and t == "user_files"]
    assert insert_calls[0][2]["mime_type"] == "application/octet-stream"


def test_upload_db_insert_fails_cleans_disk(app_factory, tmp_path, monkeypatch):
    q = _quota_row(quota_bytes=100 * 1024)
    client = _FilesClient(rows_map={"user_storage_quotas": [q]}, raise_on={"insert_row"})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = _upload(app.test_client())
    assert resp.status_code == 500
    disk_files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert disk_files == []


def test_upload_quota_update_fails_rolls_back(app_factory, tmp_path, monkeypatch):
    q = _quota_row(quota_bytes=100 * 1024)

    # First update call (quota update) raises; subsequent don't matter.
    update_call_count = [0]
    original_update = _FilesClient.update_rows_with_filters

    def _failing_update(self, table, fields, **kwargs):
        update_call_count[0] += 1
        if table == "user_storage_quotas":
            raise RuntimeError("quota update failed")
        self.calls.append(("update", table, dict(fields)))

    client = _FilesClient(rows_map={"user_storage_quotas": [q]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    monkeypatch.setattr(_FilesClient, "update_rows_with_filters", _failing_update)

    resp = _upload(app.test_client())
    assert resp.status_code == 500
    # File record should have been deleted as rollback
    delete_calls = [(op, t) for op, t, *_ in client.calls if op == "delete" and t == "user_files"]
    assert len(delete_calls) == 1
    # Disk file should be gone
    disk_files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert disk_files == []


def test_upload_admin_bypasses_quota(app_factory, tmp_path, monkeypatch):
    client = _FilesClient()
    ctx = _make_ctx(client, is_admin_ids={"admin-1"})
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: ADMIN)
    resp = _upload(app.test_client())
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_upload_success_creates_file_on_disk(app_factory, tmp_path, monkeypatch):
    q = _quota_row(quota_bytes=100 * 1024)
    client = _FilesClient(rows_map={"user_storage_quotas": [q]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = _upload(app.test_client(), data_bytes=b"hello world")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["file"]["original_name"] == "test.txt"
    disk_files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert len(disk_files) == 1
    assert disk_files[0].read_bytes() == b"hello world"


def test_upload_success_increments_quota(app_factory, tmp_path, monkeypatch):
    q = _quota_row(quota_bytes=100 * 1024, used_bytes=0)
    client = _FilesClient(rows_map={"user_storage_quotas": [q]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    _upload(app.test_client(), data_bytes=b"hello")
    update_calls = [(op, t, f) for op, t, f in client.calls if op == "update" and t == "user_storage_quotas"]
    assert len(update_calls) == 1
    assert update_calls[0][2]["used_bytes"] == 5  # len(b"hello")


def test_upload_filename_truncated_to_255(app_factory, tmp_path, monkeypatch):
    q = _quota_row(quota_bytes=100 * 1024)
    client = _FilesClient(rows_map={"user_storage_quotas": [q]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    long_name = "a" * 260 + ".txt"
    resp = _upload(app.test_client(), data_bytes=b"data", filename=long_name)
    assert resp.status_code == 200
    insert_calls = [(op, t, d) for op, t, d in client.calls if op == "insert" and t == "user_files"]
    assert len(insert_calls[0][2]["original_name"]) == 255


# ---------------------------------------------------------------------------
# 7. GET /api/files/download/<file_id>
# ---------------------------------------------------------------------------

def test_download_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = app.test_client().get(f"/api/files/download/{FILE_ID}")
    assert resp.status_code == 401


def test_download_file_not_found(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().get(f"/api/files/download/{FILE_ID}")
    assert resp.status_code == 404


def test_download_non_owner_denied(app_factory, tmp_path, monkeypatch):
    other_file = _file_row(user_id="other-user")
    client = _FilesClient(rows_map={"user_files": [other_file]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().get(f"/api/files/download/{FILE_ID}")
    assert resp.status_code == 403


def test_download_admin_can_access_any_file(app_factory, tmp_path, monkeypatch):
    stored = uuid.uuid4().hex
    user_dir = tmp_path / "other-user"
    user_dir.mkdir()
    (user_dir / stored).write_bytes(b"secret data")
    other_file = _file_row(user_id="other-user", stored_name=stored)
    client = _FilesClient(rows_map={"user_files": [other_file]})
    ctx = _make_ctx(client, is_admin_ids={"admin-1"})
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: ADMIN)
    resp = app.test_client().get(f"/api/files/download/{FILE_ID}")
    assert resp.status_code == 200


def test_download_file_missing_from_disk(app_factory, tmp_path, monkeypatch):
    row = _file_row(stored_name=uuid.uuid4().hex)
    client = _FilesClient(rows_map={"user_files": [row]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().get(f"/api/files/download/{FILE_ID}")
    assert resp.status_code == 404


def test_download_success(app_factory, tmp_path, monkeypatch):
    stored = uuid.uuid4().hex
    user_dir = tmp_path / MEMBER["id"]
    user_dir.mkdir()
    (user_dir / stored).write_bytes(b"file contents")
    row = _file_row(stored_name=stored, mime_type="text/plain")
    client = _FilesClient(rows_map={"user_files": [row]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().get(f"/api/files/download/{FILE_ID}")
    assert resp.status_code == 200
    assert b"file contents" in resp.data
    assert "test.txt" in resp.headers.get("Content-Disposition", "")


def test_download_dangerous_mime_served_as_octet_stream(app_factory, tmp_path, monkeypatch):
    stored = uuid.uuid4().hex
    user_dir = tmp_path / MEMBER["id"]
    user_dir.mkdir()
    (user_dir / stored).write_bytes(b"<html><body>hack</body></html>")
    row = _file_row(stored_name=stored, mime_type="text/html")
    client = _FilesClient(rows_map={"user_files": [row]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().get(f"/api/files/download/{FILE_ID}")
    assert resp.status_code == 200
    assert resp.content_type.startswith("application/octet-stream")


# ---------------------------------------------------------------------------
# 8. DELETE /api/files/<file_id>
# ---------------------------------------------------------------------------

def test_delete_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = app.test_client().delete(f"/api/files/{FILE_ID}")
    assert resp.status_code == 401


def test_delete_not_found(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().delete(f"/api/files/{FILE_ID}")
    assert resp.status_code == 404


def test_delete_non_owner_denied(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"user_files": [_file_row(user_id="other-user")]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().delete(f"/api/files/{FILE_ID}")
    assert resp.status_code == 403


def test_delete_admin_can_delete_any(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={
        "user_files": [_file_row(user_id="other-user", stored_name=uuid.uuid4().hex)],
        "user_storage_quotas": [],
    })
    ctx = _make_ctx(client, is_admin_ids={"admin-1"})
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: ADMIN)
    resp = app.test_client().delete(f"/api/files/{FILE_ID}")
    assert resp.status_code == 200


def test_delete_success_removes_disk_file_and_db_record(app_factory, tmp_path, monkeypatch):
    stored = uuid.uuid4().hex
    user_dir = tmp_path / MEMBER["id"]
    user_dir.mkdir()
    disk_file = user_dir / stored
    disk_file.write_bytes(b"goodbye")
    client = _FilesClient(rows_map={
        "user_files": [_file_row(stored_name=stored)],
        "user_storage_quotas": [_quota_row()],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().delete(f"/api/files/{FILE_ID}")
    assert resp.status_code == 200
    assert not disk_file.exists()
    delete_calls = [(op, t) for op, t, *_ in client.calls if op == "delete" and t == "user_files"]
    assert len(delete_calls) == 1


# ---------------------------------------------------------------------------
# 9. _delete_file_record helper
# ---------------------------------------------------------------------------

def test_delete_file_record_removes_disk_file(app_factory, tmp_path, monkeypatch):
    stored = uuid.uuid4().hex
    user_dir = tmp_path / MEMBER["id"]
    user_dir.mkdir()
    disk_file = user_dir / stored
    disk_file.write_bytes(b"data")
    client = _FilesClient(rows_map={"user_storage_quotas": [_quota_row()]})
    ctx = _make_ctx(client)
    row = _file_row(stored_name=stored)
    app = _make_app(app_factory, ctx, tmp_path)
    with app.app_context():
        files_mod._delete_file_record(ctx, row)
    assert not disk_file.exists()


def test_delete_file_record_missing_disk_file_no_error(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"user_storage_quotas": [_quota_row()]})
    ctx = _make_ctx(client)
    row = _file_row(stored_name=uuid.uuid4().hex)
    app = _make_app(app_factory, ctx, tmp_path)
    with app.app_context():
        files_mod._delete_file_record(ctx, row)  # Must not raise


def test_delete_file_record_zero_size_skips_quota_update(app_factory, tmp_path):
    client = _FilesClient(rows_map={"user_storage_quotas": [_quota_row()]})
    ctx = _make_ctx(client)
    row = {**_file_row(), "size_bytes": 0}
    app = _make_app(app_factory, ctx, tmp_path)
    with app.app_context():
        files_mod._delete_file_record(ctx, row)
    update_calls = [(op, t) for op, t, *_ in client.calls if op == "update" and t == "user_storage_quotas"]
    assert len(update_calls) == 0


def test_delete_file_record_decrements_quota(app_factory, tmp_path):
    q = _quota_row(used_bytes=100)
    client = _FilesClient(rows_map={"user_storage_quotas": [q]})
    ctx = _make_ctx(client)
    row = {**_file_row(), "size_bytes": 42}
    app = _make_app(app_factory, ctx, tmp_path)
    with app.app_context():
        files_mod._delete_file_record(ctx, row)
    update_calls = [(op, t, f) for op, t, f in client.calls if op == "update" and t == "user_storage_quotas"]
    assert len(update_calls) == 1
    assert update_calls[0][2]["used_bytes"] == 58  # 100 - 42


def test_delete_file_record_quota_update_failure_doesnt_raise(app_factory, tmp_path, monkeypatch):
    def _bad_update(self, table, fields, **kwargs):
        raise RuntimeError("db down")

    client = _FilesClient(rows_map={"user_storage_quotas": [_quota_row(used_bytes=50)]})
    ctx = _make_ctx(client)
    row = {**_file_row(), "size_bytes": 10}
    monkeypatch.setattr(_FilesClient, "update_rows_with_filters", _bad_update)
    app = _make_app(app_factory, ctx, tmp_path)
    with app.app_context():
        files_mod._delete_file_record(ctx, row)  # Must not raise


# ---------------------------------------------------------------------------
# 10. Admin quota endpoints
# ---------------------------------------------------------------------------

def _make_admin_ctx(client=None, *, query_rows=None):
    c = client or _FilesClient()
    return _make_ctx(c, is_admin_ids={"admin-1"}, query_rows=query_rows)


def test_admin_quota_list_non_admin(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (None, (flask.jsonify({"ok": False}), 403)))
    resp = app.test_client().get("/api/admin/files/quota/list")
    assert resp.status_code == 403


def test_admin_quota_list_returns_quotas(app_factory, tmp_path, monkeypatch):
    quota_entry = {**_quota_row(), "first_name": "Alice", "last_name": "Smith", "email": "a@example.com"}
    ctx = _make_admin_ctx(query_rows=[quota_entry])
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().get("/api/admin/files/quota/list")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert len(body["quotas"]) == 1
    assert body["quotas"][0]["status"] == "approved"


def test_admin_quota_set_missing_user_id(app_factory, tmp_path, monkeypatch):
    ctx = _make_admin_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().post("/api/admin/files/quota/set", json={"status": "approved", "quota_bytes": 1000})
    assert resp.status_code == 400
    assert "user_id" in resp.get_json()["message"]


def test_admin_quota_set_invalid_status(app_factory, tmp_path, monkeypatch):
    ctx = _make_admin_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().post("/api/admin/files/quota/set", json={"user_id": "u-1", "status": "maybe"})
    assert resp.status_code == 400
    assert "status" in resp.get_json()["message"]


def test_admin_quota_set_approved_invalid_bytes(app_factory, tmp_path, monkeypatch):
    ctx = _make_admin_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().post(
        "/api/admin/files/quota/set",
        json={"user_id": "u-1", "status": "approved", "quota_bytes": -1},
    )
    assert resp.status_code == 400


def test_admin_quota_set_user_not_found(app_factory, tmp_path, monkeypatch):
    ctx = _make_admin_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().post(
        "/api/admin/files/quota/set",
        json={"user_id": "missing-user", "status": "approved", "quota_bytes": 1000},
    )
    assert resp.status_code == 404


def test_admin_quota_set_approve_existing(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={
        "users": [{"id": "u-1"}],
        "user_storage_quotas": [_quota_row(status="pending")],
    })
    ctx = _make_admin_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().post(
        "/api/admin/files/quota/set",
        json={"user_id": "u-1", "status": "approved", "quota_bytes": 5 * 1024 * 1024},
    )
    assert resp.status_code == 200
    update_calls = [(op, t, f) for op, t, f in client.calls if op == "update" and t == "user_storage_quotas"]
    assert len(update_calls) == 1
    assert update_calls[0][2]["status"] == "approved"
    assert update_calls[0][2]["approved_at"] is not None


def test_admin_quota_set_deny_existing(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={
        "users": [{"id": "u-1"}],
        "user_storage_quotas": [_quota_row(status="pending")],
    })
    ctx = _make_admin_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().post(
        "/api/admin/files/quota/set",
        json={"user_id": "u-1", "status": "denied"},
    )
    assert resp.status_code == 200
    update_calls = [(op, t, f) for op, t, f in client.calls if op == "update" and t == "user_storage_quotas"]
    assert update_calls[0][2]["status"] == "denied"
    assert "approved_at" not in update_calls[0][2]


def test_admin_quota_set_no_existing_inserts(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"users": [{"id": "u-1"}]})
    ctx = _make_admin_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().post(
        "/api/admin/files/quota/set",
        json={"user_id": "u-1", "status": "approved", "quota_bytes": 1024},
    )
    assert resp.status_code == 200
    insert_calls = [(op, t) for op, t, *_ in client.calls if op == "insert" and t == "user_storage_quotas"]
    assert len(insert_calls) == 1


# ---------------------------------------------------------------------------
# 11. Admin file list + delete
# ---------------------------------------------------------------------------

def test_admin_files_list_non_admin(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (None, (flask.jsonify({"ok": False}), 403)))
    resp = app.test_client().get("/api/admin/files/list")
    assert resp.status_code == 403


def test_admin_files_list_returns_all_files(app_factory, tmp_path, monkeypatch):
    file_entry = {**_file_row(), "first_name": "Alice", "last_name": "Smith", "email": "a@ex.com", "download_count": 0}
    ctx = _make_admin_ctx(query_rows=[file_entry])
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().get("/api/admin/files/list")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["total_files"] == 1


def test_admin_files_delete_not_found(app_factory, tmp_path, monkeypatch):
    ctx = _make_admin_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().delete("/api/admin/files/missing-id")
    assert resp.status_code == 404


def test_admin_files_delete_success(app_factory, tmp_path, monkeypatch):
    stored = uuid.uuid4().hex
    client = _FilesClient(rows_map={
        "user_files": [_file_row(stored_name=stored)],
        "user_storage_quotas": [],
    })
    ctx = _make_admin_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().delete(f"/api/admin/files/{FILE_ID}")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 12. Folder endpoints
# ---------------------------------------------------------------------------

def test_folders_list_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = app.test_client().get("/api/files/folders")
    assert resp.status_code == 401


def test_folders_list_authenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx(query_rows=[_folder_row()])
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().get("/api/files/folders")
    assert resp.status_code == 200
    assert len(resp.get_json()["folders"]) == 1


def test_folders_create_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = app.test_client().post("/api/files/folders", json={"name": "My Folder"})
    assert resp.status_code == 401


def test_folders_create_blank_name(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post("/api/files/folders", json={"name": "   "})
    assert resp.status_code == 400


def test_folders_create_success(app_factory, tmp_path, monkeypatch):
    client = _FilesClient()
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post("/api/files/folders", json={"name": "New Folder"})
    assert resp.status_code == 200
    assert resp.get_json()["folder"]["name"] == "New Folder"
    insert_calls = [(op, t) for op, t, *_ in client.calls if op == "insert" and t == "file_folders"]
    assert len(insert_calls) == 1


def test_folders_delete_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = app.test_client().delete(f"/api/files/folders/{FOLDER_ID}")
    assert resp.status_code == 401


def test_folders_delete_not_found(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().delete(f"/api/files/folders/{FOLDER_ID}")
    assert resp.status_code == 404


def test_folders_delete_non_owner_denied(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_folders": [_folder_row(user_id="other-user")]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().delete(f"/api/files/folders/{FOLDER_ID}")
    assert resp.status_code == 403


def test_folders_delete_admin_allowed(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_folders": [_folder_row(user_id="other-user")]})
    ctx = _make_ctx(client, is_admin_ids={"admin-1"})
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: ADMIN)
    resp = app.test_client().delete(f"/api/files/folders/{FOLDER_ID}")
    assert resp.status_code == 200


def test_folders_delete_success(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_folders": [_folder_row()]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().delete(f"/api/files/folders/{FOLDER_ID}")
    assert resp.status_code == 200
    delete_calls = [(op, t) for op, t, *_ in client.calls if op == "delete" and t == "file_folders"]
    assert len(delete_calls) == 1


def test_folder_contents_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = app.test_client().get(f"/api/files/folders/{FOLDER_ID}")
    assert resp.status_code == 401


def test_folder_contents_not_found(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().get(f"/api/files/folders/{FOLDER_ID}")
    assert resp.status_code == 404


def test_folder_contents_non_owner_denied(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_folders": [_folder_row(user_id="other-user")]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().get(f"/api/files/folders/{FOLDER_ID}")
    assert resp.status_code == 403


def test_folder_contents_success(app_factory, tmp_path, monkeypatch):
    f_row = {**_file_row(), "added_at": "2024-01-01T00:00:00+00:00"}
    client = _FilesClient(rows_map={"file_folders": [_folder_row()]})
    ctx = _make_ctx(client, query_rows=[f_row])
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().get(f"/api/files/folders/{FOLDER_ID}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["files"]) == 1


def test_folder_add_item_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = app.test_client().post(f"/api/files/folders/{FOLDER_ID}/items", json={"file_id": FILE_ID})
    assert resp.status_code == 401


def test_folder_add_item_folder_not_found(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post(f"/api/files/folders/{FOLDER_ID}/items", json={"file_id": FILE_ID})
    assert resp.status_code == 404


def test_folder_add_item_non_owner_denied(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_folders": [_folder_row(user_id="other-user")]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post(f"/api/files/folders/{FOLDER_ID}/items", json={"file_id": FILE_ID})
    assert resp.status_code == 403


def test_folder_add_item_blank_file_id(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_folders": [_folder_row()]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post(f"/api/files/folders/{FOLDER_ID}/items", json={"file_id": ""})
    assert resp.status_code == 400
    assert "file_id" in resp.get_json()["message"]


def test_folder_add_item_file_not_owned(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={
        "file_folders": [_folder_row()],
        "user_files": [],  # file not found for this user
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post(f"/api/files/folders/{FOLDER_ID}/items", json={"file_id": FILE_ID})
    assert resp.status_code == 404


def test_folder_add_item_already_in_folder(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={
        "file_folders": [_folder_row()],
        "user_files": [_file_row()],
        "file_folder_items": [{"id": "item-1", "folder_id": FOLDER_ID, "file_id": FILE_ID}],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post(f"/api/files/folders/{FOLDER_ID}/items", json={"file_id": FILE_ID})
    assert resp.status_code == 200
    assert "already" in resp.get_json()["message"].lower()


def test_folder_add_item_success(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={
        "file_folders": [_folder_row()],
        "user_files": [_file_row()],
        "file_folder_items": [],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post(f"/api/files/folders/{FOLDER_ID}/items", json={"file_id": FILE_ID})
    assert resp.status_code == 200
    insert_calls = [(op, t) for op, t, *_ in client.calls if op == "insert" and t == "file_folder_items"]
    assert len(insert_calls) == 1


def test_folder_remove_item_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = app.test_client().delete(f"/api/files/folders/{FOLDER_ID}/items/{FILE_ID}")
    assert resp.status_code == 401


def test_folder_remove_item_not_found(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().delete(f"/api/files/folders/{FOLDER_ID}/items/{FILE_ID}")
    assert resp.status_code == 404


def test_folder_remove_item_non_owner_denied(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_folders": [_folder_row(user_id="other-user")]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().delete(f"/api/files/folders/{FOLDER_ID}/items/{FILE_ID}")
    assert resp.status_code == 403


def test_folder_remove_item_success(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_folders": [_folder_row()]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().delete(f"/api/files/folders/{FOLDER_ID}/items/{FILE_ID}")
    assert resp.status_code == 200
    delete_calls = [(op, t) for op, t, *_ in client.calls if op == "delete" and t == "file_folder_items"]
    assert len(delete_calls) == 1


# ---------------------------------------------------------------------------
# 13. Member share link endpoints
# ---------------------------------------------------------------------------

def test_share_create_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = app.test_client().post("/api/files/share", json={"target_type": "file", "target_id": FILE_ID})
    assert resp.status_code == 401


def test_share_create_invalid_target_type(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post("/api/files/share", json={"target_type": "image", "target_id": FILE_ID})
    assert resp.status_code == 400
    assert "target_type" in resp.get_json()["message"]


def test_share_create_blank_target_id(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post("/api/files/share", json={"target_type": "file", "target_id": ""})
    assert resp.status_code == 400
    assert "target_id" in resp.get_json()["message"]


def test_share_create_file_not_found(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post("/api/files/share", json={"target_type": "file", "target_id": FILE_ID})
    assert resp.status_code == 404


def test_share_create_folder_not_found(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post("/api/files/share", json={"target_type": "folder", "target_id": FOLDER_ID})
    assert resp.status_code == 404


def test_share_create_returns_existing_link(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={
        "user_files": [_file_row()],
        "file_share_links": [_link_row()],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post("/api/files/share", json={"target_type": "file", "target_id": FILE_ID})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["existing"] is True
    assert body["link_id"] == LINK_ID


def test_share_create_new_file_link(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={
        "user_files": [_file_row()],
        "file_share_links": [],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post("/api/files/share", json={"target_type": "file", "target_id": FILE_ID})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["existing"] is False
    insert_calls = [(op, t, d) for op, t, d in client.calls if op == "insert" and t == "file_share_links"]
    assert len(insert_calls) == 1
    assert insert_calls[0][2]["file_id"] == FILE_ID
    assert insert_calls[0][2]["folder_id"] is None


def test_share_create_new_folder_link(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={
        "file_folders": [_folder_row()],
        "file_share_links": [],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().post("/api/files/share", json={"target_type": "folder", "target_id": FOLDER_ID})
    assert resp.status_code == 200
    insert_calls = [(op, t, d) for op, t, d in client.calls if op == "insert" and t == "file_share_links"]
    assert insert_calls[0][2]["folder_id"] == FOLDER_ID
    assert insert_calls[0][2]["file_id"] is None


def test_share_list_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = app.test_client().get("/api/files/share")
    assert resp.status_code == 401


def test_share_list_returns_links(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx(query_rows=[_link_row()])
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().get("/api/files/share")
    assert resp.status_code == 200
    assert len(resp.get_json()["links"]) == 1


def test_share_update_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = app.test_client().patch(f"/api/files/share/{LINK_ID}", json={"is_enabled": False})
    assert resp.status_code == 401


def test_share_update_not_found(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().patch(f"/api/files/share/{LINK_ID}", json={"is_enabled": False})
    assert resp.status_code == 404


def test_share_update_non_owner_denied(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_share_links": [_link_row(created_by="other-user")]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().patch(f"/api/files/share/{LINK_ID}", json={"is_enabled": False})
    assert resp.status_code == 403


def test_share_update_missing_is_enabled(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_share_links": [_link_row()]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().patch(f"/api/files/share/{LINK_ID}", json={})
    assert resp.status_code == 400
    assert "is_enabled" in resp.get_json()["message"]


def test_share_update_success(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_share_links": [_link_row()]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().patch(f"/api/files/share/{LINK_ID}", json={"is_enabled": False})
    assert resp.status_code == 200
    update_calls = [(op, t, f) for op, t, f in client.calls if op == "update" and t == "file_share_links"]
    assert len(update_calls) == 1
    assert update_calls[0][2]["is_enabled"] is False


def test_share_delete_unauthenticated(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: None)
    resp = app.test_client().delete(f"/api/files/share/{LINK_ID}")
    assert resp.status_code == 401


def test_share_delete_not_found(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().delete(f"/api/files/share/{LINK_ID}")
    assert resp.status_code == 404


def test_share_delete_non_owner_denied(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_share_links": [_link_row(created_by="other-user")]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().delete(f"/api/files/share/{LINK_ID}")
    assert resp.status_code == 403


def test_share_delete_admin_can_delete_any(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_share_links": [_link_row(created_by="other-user")]})
    ctx = _make_ctx(client, is_admin_ids={"admin-1"})
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: ADMIN)
    resp = app.test_client().delete(f"/api/files/share/{LINK_ID}")
    assert resp.status_code == 200


def test_share_delete_owner_success(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_share_links": [_link_row()]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "get_request_user", lambda _ctx: MEMBER)
    resp = app.test_client().delete(f"/api/files/share/{LINK_ID}")
    assert resp.status_code == 200
    delete_calls = [(op, t) for op, t, *_ in client.calls if op == "delete" and t == "file_share_links"]
    assert len(delete_calls) == 1


# ---------------------------------------------------------------------------
# 14. Admin share link endpoints
# ---------------------------------------------------------------------------

def test_admin_share_list_non_admin(app_factory, tmp_path, monkeypatch):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (None, (flask.jsonify({"ok": False}), 403)))
    resp = app.test_client().get("/api/admin/share/list")
    assert resp.status_code == 403


def test_admin_share_list_returns_links(app_factory, tmp_path, monkeypatch):
    link_entry = {**_link_row(), "first_name": "Alice", "last_name": "Smith", "email": "a@ex.com"}
    ctx = _make_admin_ctx(query_rows=[link_entry])
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().get("/api/admin/share/list")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["links"]) == 1
    assert body["links"][0]["owner_email"] == "a@ex.com"


def test_admin_share_update_missing_is_enabled(app_factory, tmp_path, monkeypatch):
    ctx = _make_admin_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().patch(f"/api/admin/share/{LINK_ID}", json={})
    assert resp.status_code == 400


def test_admin_share_update_not_found(app_factory, tmp_path, monkeypatch):
    ctx = _make_admin_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().patch(f"/api/admin/share/{LINK_ID}", json={"is_enabled": False})
    assert resp.status_code == 404


def test_admin_share_update_success(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_share_links": [_link_row()]})
    ctx = _make_admin_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().patch(f"/api/admin/share/{LINK_ID}", json={"is_enabled": False})
    assert resp.status_code == 200


def test_admin_share_delete_not_found(app_factory, tmp_path, monkeypatch):
    ctx = _make_admin_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().delete(f"/api/admin/share/{LINK_ID}")
    assert resp.status_code == 404


def test_admin_share_delete_success(app_factory, tmp_path, monkeypatch):
    client = _FilesClient(rows_map={"file_share_links": [_link_row()]})
    ctx = _make_admin_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    monkeypatch.setattr(files_mod, "require_admin", lambda _ctx: (ADMIN, None))
    resp = app.test_client().delete(f"/api/admin/share/{LINK_ID}")
    assert resp.status_code == 200
    delete_calls = [(op, t) for op, t, *_ in client.calls if op == "delete" and t == "file_share_links"]
    assert len(delete_calls) == 1
