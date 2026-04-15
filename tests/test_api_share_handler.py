from __future__ import annotations

import io
import uuid
import zipfile
from types import SimpleNamespace

import flask
import pytest

import app.api_handlers.share as share_mod

# ---------------------------------------------------------------------------
# Shared fakes (mirror the pattern from test_api_files_handler.py)
# ---------------------------------------------------------------------------

FILE_ID   = "file-001"
FOLDER_ID = "folder-001"
LINK_ID   = "link-001"
USER_ID   = "user-1"


class _ShareClient:
    """Minimal stub DB client for share handler tests."""

    def __init__(self, *, rows_map=None, raise_on_get=False):
        self.rows_map: dict = rows_map or {}
        self.raise_on_get = raise_on_get
        self.calls: list = []
        self._call_counts: dict[str, int] = {}

    def get_rows_with_filters(self, table, equalities=None, **kwargs):
        if self.raise_on_get:
            raise RuntimeError("db error")
        self.calls.append(("get", table, equalities or {}))
        rows = self.rows_map.get(table, [])
        if rows and isinstance(rows[0], list):   # side-effect queue
            idx = self._call_counts.get(table, 0)
            result = rows[idx] if idx < len(rows) else []
            self._call_counts[table] = idx + 1
        else:
            result = rows
        return result, len(result)

    def update_rows_with_filters(self, table, fields, **kwargs):
        self.calls.append(("update", table, dict(fields)))


class _ShareInterface:
    def __init__(self, client, *, query_rows=None):
        self.client = client
        self._query_rows: list = query_rows or []

    def execute_query(self, sql, params=None):
        return self._query_rows


def _make_ctx(client=None, *, query_rows=None):
    c = client or _ShareClient()
    return SimpleNamespace(
        auth_token_name="session",
        interface=_ShareInterface(c, query_rows=query_rows),
        fcr=SimpleNamespace(find=lambda _: None),
    )


def _make_app(app_factory, ctx, tmp_path):
    app = app_factory(share_mod.register, ctx)
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    return app


def _link_row(*, target_type="file", is_enabled=True, file_id=FILE_ID, folder_id=None):
    return {
        "id": LINK_ID,
        "created_by": USER_ID,
        "target_type": target_type,
        "file_id": file_id,
        "folder_id": folder_id,
        "is_enabled": is_enabled,
        "download_count": 3,
        "created_at": "2024-01-01T00:00:00+00:00",
        "last_accessed_at": None,
    }


def _file_row(*, stored_name=None, mime_type="text/plain", size_bytes=42):
    return {
        "id": FILE_ID,
        "user_id": USER_ID,
        "original_name": "hello.txt",
        "stored_name": stored_name or uuid.uuid4().hex,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
    }


def _folder_row():
    return {
        "id": FOLDER_ID,
        "user_id": USER_ID,
        "name": "Shared Folder",
    }


# ---------------------------------------------------------------------------
# 1. Pure helpers — no Flask context required
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mime_type,expected", [
    (None,                        "application/octet-stream"),
    ("",                          "application/octet-stream"),
    ("text/html",                 "application/octet-stream"),
    ("text/html; charset=utf-8",  "application/octet-stream"),
    ("application/javascript",    "application/octet-stream"),
    ("text/javascript",           "application/octet-stream"),
    ("application/x-sh",          "application/octet-stream"),
    ("application/x-httpd-php",   "application/octet-stream"),
    ("text/plain",                "text/plain"),
    ("application/pdf",           "application/pdf"),
    ("image/jpeg",                "image/jpeg"),
])
def test_safe_mime_type(mime_type, expected):
    assert share_mod._safe_mime_type(mime_type) == expected


def test_safe_read_path_valid(tmp_path):
    result = share_mod._safe_read_path(str(tmp_path), "user-1", "abcdef1234")
    assert result.startswith(str(tmp_path))
    assert "user-1" in result
    assert "abcdef1234" in result


def test_safe_read_path_traversal_via_stored_name(tmp_path):
    with pytest.raises(ValueError, match="Path traversal"):
        share_mod._safe_read_path(str(tmp_path), "user-1", "../../../etc/passwd")


def test_safe_read_path_traversal_via_user_id(tmp_path):
    with pytest.raises(ValueError, match="Path traversal"):
        share_mod._safe_read_path(str(tmp_path), "../../root", "stored")


# ---------------------------------------------------------------------------
# 2. _build_folder_zip
# ---------------------------------------------------------------------------

def test_build_folder_zip_returns_none_when_too_large(tmp_path):
    big = share_mod._MAX_ZIP_BYTES + 1
    files = [{"user_id": USER_ID, "stored_name": "x", "original_name": "x.bin", "size_bytes": big}]
    result = share_mod._build_folder_zip(files, str(tmp_path))
    assert result is None


def test_build_folder_zip_empty_list_returns_empty_zip(tmp_path):
    result = share_mod._build_folder_zip([], str(tmp_path))
    assert result is not None
    with zipfile.ZipFile(result) as zf:
        assert zf.namelist() == []


def test_build_folder_zip_single_file(tmp_path):
    stored = uuid.uuid4().hex
    user_dir = tmp_path / USER_ID
    user_dir.mkdir()
    (user_dir / stored).write_bytes(b"zip me")
    files = [{"user_id": USER_ID, "stored_name": stored, "original_name": "doc.txt", "size_bytes": 6}]
    result = share_mod._build_folder_zip(files, str(tmp_path))
    assert result is not None
    with zipfile.ZipFile(result) as zf:
        assert "doc.txt" in zf.namelist()
        assert zf.read("doc.txt") == b"zip me"


def test_build_folder_zip_skips_missing_disk_file(tmp_path):
    files = [{"user_id": USER_ID, "stored_name": "does_not_exist", "original_name": "gone.txt", "size_bytes": 10}]
    result = share_mod._build_folder_zip(files, str(tmp_path))
    assert result is not None
    with zipfile.ZipFile(result) as zf:
        assert zf.namelist() == []


def test_build_folder_zip_skips_path_traversal_file(tmp_path):
    # A file whose stored_name would escape the upload root
    files = [{"user_id": USER_ID, "stored_name": "../../../etc/passwd", "original_name": "evil.txt", "size_bytes": 10}]
    result = share_mod._build_folder_zip(files, str(tmp_path))
    assert result is not None
    with zipfile.ZipFile(result) as zf:
        assert zf.namelist() == []


def test_build_folder_zip_deduplicates_filenames(tmp_path):
    user_dir = tmp_path / USER_ID
    user_dir.mkdir()
    stored_a = uuid.uuid4().hex
    stored_b = uuid.uuid4().hex
    (user_dir / stored_a).write_bytes(b"aaa")
    (user_dir / stored_b).write_bytes(b"bbb")
    files = [
        {"user_id": USER_ID, "stored_name": stored_a, "original_name": "report.txt", "size_bytes": 3},
        {"user_id": USER_ID, "stored_name": stored_b, "original_name": "report.txt", "size_bytes": 3},
    ]
    result = share_mod._build_folder_zip(files, str(tmp_path))
    assert result is not None
    with zipfile.ZipFile(result) as zf:
        names = zf.namelist()
        assert len(names) == 2
        assert "report.txt" in names
        # Second entry should be deduplicated
        assert any("(1)" in n for n in names)


def test_build_folder_zip_multiple_files(tmp_path):
    user_dir = tmp_path / USER_ID
    user_dir.mkdir()
    stored_a = uuid.uuid4().hex
    stored_b = uuid.uuid4().hex
    (user_dir / stored_a).write_bytes(b"alpha")
    (user_dir / stored_b).write_bytes(b"beta")
    files = [
        {"user_id": USER_ID, "stored_name": stored_a, "original_name": "a.txt", "size_bytes": 5},
        {"user_id": USER_ID, "stored_name": stored_b, "original_name": "b.txt", "size_bytes": 4},
    ]
    result = share_mod._build_folder_zip(files, str(tmp_path))
    assert result is not None
    with zipfile.ZipFile(result) as zf:
        assert set(zf.namelist()) == {"a.txt", "b.txt"}


# ---------------------------------------------------------------------------
# 3. _resolve_link (requires Flask app context for flask.jsonify)
# ---------------------------------------------------------------------------

def test_resolve_link_db_error_returns_404(app_factory, tmp_path):
    client = _ShareClient(raise_on_get=True)
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    with app.app_context():
        link, err = share_mod._resolve_link(ctx, "some-id")
    assert link is None
    assert err is not None


def test_resolve_link_not_found_returns_404(app_factory, tmp_path):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    with app.app_context():
        link, err = share_mod._resolve_link(ctx, "missing-id")
    assert link is None
    assert err is not None


def test_resolve_link_found_no_require_enabled(app_factory, tmp_path):
    client = _ShareClient(rows_map={"file_share_links": [_link_row(is_enabled=False)]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    with app.app_context():
        link, err = share_mod._resolve_link(ctx, LINK_ID, require_enabled=False)
    assert link is not None
    assert err is None


def test_resolve_link_enabled_when_required(app_factory, tmp_path):
    client = _ShareClient(rows_map={"file_share_links": [_link_row(is_enabled=True)]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    with app.app_context():
        link, err = share_mod._resolve_link(ctx, LINK_ID, require_enabled=True)
    assert link is not None
    assert err is None


def test_resolve_link_disabled_when_required_returns_403(app_factory, tmp_path):
    client = _ShareClient(rows_map={"file_share_links": [_link_row(is_enabled=False)]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    with app.app_context():
        link, err = share_mod._resolve_link(ctx, LINK_ID, require_enabled=True)
    assert link is None
    assert err is not None
    # err is (response, status_code) tuple
    assert err[1] == 403


# ---------------------------------------------------------------------------
# 4. _record_access
# ---------------------------------------------------------------------------

def test_record_access_increments_count():
    client = _ShareClient(rows_map={"file_share_links": [_link_row()]})
    ctx = _make_ctx(client)
    share_mod._record_access(ctx, LINK_ID)
    update_calls = [(op, t, f) for op, t, f in client.calls if op == "update" and t == "file_share_links"]
    assert len(update_calls) == 1
    assert update_calls[0][2]["download_count"] == 4  # was 3, now 4


def test_record_access_no_rows_is_noop():
    client = _ShareClient()
    ctx = _make_ctx(client)
    share_mod._record_access(ctx, LINK_ID)  # must not raise
    update_calls = [c for c in client.calls if c[0] == "update"]
    assert update_calls == []


def test_record_access_exception_is_swallowed():
    client = _ShareClient(raise_on_get=True)
    ctx = _make_ctx(client)
    share_mod._record_access(ctx, LINK_ID)  # must not raise


# ---------------------------------------------------------------------------
# 5. GET /api/share/<link_id>  — metadata endpoint
# ---------------------------------------------------------------------------

def test_share_get_link_not_found(app_factory, tmp_path):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/nonexistent")
    assert resp.status_code == 404


def test_share_get_file_link_returns_metadata(app_factory, tmp_path):
    file_entry = _file_row()
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row()],
        "user_files": [file_entry],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["target_type"] == "file"
    assert body["name"] == "hello.txt"
    assert body["size_bytes"] == 42


def test_share_get_file_link_file_gone(app_factory, tmp_path):
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row()],
        "user_files": [],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}")
    assert resp.status_code == 404


def test_share_get_folder_link_returns_metadata(app_factory, tmp_path):
    folder_entry = _folder_row()
    file_entry = _file_row()
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row(target_type="folder", file_id=None, folder_id=FOLDER_ID)],
        "file_folders": [folder_entry],
    })
    ctx = _make_ctx(client, query_rows=[file_entry])
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["target_type"] == "folder"
    assert body["name"] == "Shared Folder"
    assert len(body["files"]) == 1


def test_share_get_folder_link_folder_gone(app_factory, tmp_path):
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row(target_type="folder", file_id=None, folder_id=FOLDER_ID)],
        "file_folders": [],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}")
    assert resp.status_code == 404


def test_share_get_disabled_link_still_returns_metadata(app_factory, tmp_path):
    """Metadata endpoint does NOT require_enabled — disabled links still show info."""
    file_entry = _file_row()
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row(is_enabled=False)],
        "user_files": [file_entry],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}")
    assert resp.status_code == 200
    assert resp.get_json()["is_enabled"] is False


# ---------------------------------------------------------------------------
# 6. GET /api/share/<link_id>/download
# ---------------------------------------------------------------------------

def test_share_download_link_not_found(app_factory, tmp_path):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/download")
    assert resp.status_code == 404


def test_share_download_disabled_link_rejected(app_factory, tmp_path):
    client = _ShareClient(rows_map={"file_share_links": [_link_row(is_enabled=False)]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/download")
    assert resp.status_code == 403


def test_share_download_file_gone_from_db(app_factory, tmp_path):
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row()],
        "user_files": [],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/download")
    assert resp.status_code == 404


def test_share_download_file_missing_from_disk(app_factory, tmp_path):
    stored = uuid.uuid4().hex  # exists in DB but not on disk
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row()],
        "user_files": [_file_row(stored_name=stored)],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/download")
    assert resp.status_code == 404


def test_share_download_file_path_traversal_blocked(app_factory, tmp_path):
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row()],
        "user_files": [_file_row(stored_name="../../../etc/passwd")],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/download")
    assert resp.status_code == 404


def test_share_download_file_success(app_factory, tmp_path):
    stored = uuid.uuid4().hex
    user_dir = tmp_path / USER_ID
    user_dir.mkdir()
    (user_dir / stored).write_bytes(b"shared content")
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row()],
        "user_files": [_file_row(stored_name=stored, mime_type="text/plain")],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/download")
    assert resp.status_code == 200
    assert b"shared content" in resp.data
    assert "hello.txt" in resp.headers.get("Content-Disposition", "")


def test_share_download_file_dangerous_mime_served_as_octet_stream(app_factory, tmp_path):
    stored = uuid.uuid4().hex
    user_dir = tmp_path / USER_ID
    user_dir.mkdir()
    (user_dir / stored).write_bytes(b"<script>alert(1)</script>")
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row()],
        "user_files": [_file_row(stored_name=stored, mime_type="text/html")],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/download")
    assert resp.status_code == 200
    assert resp.content_type.startswith("application/octet-stream")


def test_share_download_folder_gone(app_factory, tmp_path):
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row(target_type="folder", file_id=None, folder_id=FOLDER_ID)],
        "file_folders": [],
    })
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/download")
    assert resp.status_code == 404


def test_share_download_folder_empty(app_factory, tmp_path):
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row(target_type="folder", file_id=None, folder_id=FOLDER_ID)],
        "file_folders": [_folder_row()],
    })
    ctx = _make_ctx(client, query_rows=[])   # no files in folder
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/download")
    assert resp.status_code == 404
    assert "empty" in resp.get_json()["message"].lower()


def test_share_download_folder_too_large(app_factory, tmp_path):
    big_file = {**_file_row(), "size_bytes": share_mod._MAX_ZIP_BYTES + 1}
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row(target_type="folder", file_id=None, folder_id=FOLDER_ID)],
        "file_folders": [_folder_row()],
    })
    ctx = _make_ctx(client, query_rows=[big_file])
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/download")
    assert resp.status_code == 400
    assert "large" in resp.get_json()["message"].lower()


def test_share_download_folder_success_returns_zip(app_factory, tmp_path):
    stored = uuid.uuid4().hex
    user_dir = tmp_path / USER_ID
    user_dir.mkdir()
    (user_dir / stored).write_bytes(b"zipped content")
    file_entry = _file_row(stored_name=stored, size_bytes=14)
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row(target_type="folder", file_id=None, folder_id=FOLDER_ID)],
        "file_folders": [_folder_row()],
    })
    ctx = _make_ctx(client, query_rows=[file_entry])
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/download")
    assert resp.status_code == 200
    assert resp.content_type == "application/zip"
    assert "Shared Folder" in resp.headers.get("Content-Disposition", "")
    # Verify it's a valid ZIP containing our file
    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    assert "hello.txt" in zf.namelist()
    assert zf.read("hello.txt") == b"zipped content"


# ---------------------------------------------------------------------------
# 7. GET /api/share/<link_id>/files/<file_id>
# ---------------------------------------------------------------------------

def test_share_folder_file_link_not_found(app_factory, tmp_path):
    ctx = _make_ctx()
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/files/{FILE_ID}")
    assert resp.status_code == 404


def test_share_folder_file_disabled_link_rejected(app_factory, tmp_path):
    client = _ShareClient(rows_map={"file_share_links": [_link_row(is_enabled=False)]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/files/{FILE_ID}")
    assert resp.status_code == 403


def test_share_folder_file_wrong_link_type(app_factory, tmp_path):
    """A file-type share link cannot be used to download individual folder files."""
    client = _ShareClient(rows_map={"file_share_links": [_link_row(target_type="file")]})
    ctx = _make_ctx(client)
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/files/{FILE_ID}")
    assert resp.status_code == 400
    assert "folder" in resp.get_json()["message"].lower()


def test_share_folder_file_not_in_folder(app_factory, tmp_path):
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row(target_type="folder", file_id=None, folder_id=FOLDER_ID)],
    })
    ctx = _make_ctx(client, query_rows=[])   # JOIN returns nothing
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/files/{FILE_ID}")
    assert resp.status_code == 404


def test_share_folder_file_path_traversal_blocked(app_factory, tmp_path):
    evil_file = _file_row(stored_name="../../../etc/passwd")
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row(target_type="folder", file_id=None, folder_id=FOLDER_ID)],
    })
    ctx = _make_ctx(client, query_rows=[evil_file])
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/files/{FILE_ID}")
    assert resp.status_code == 404


def test_share_folder_file_missing_from_disk(app_factory, tmp_path):
    file_entry = _file_row(stored_name=uuid.uuid4().hex)
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row(target_type="folder", file_id=None, folder_id=FOLDER_ID)],
    })
    ctx = _make_ctx(client, query_rows=[file_entry])
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/files/{FILE_ID}")
    assert resp.status_code == 404


def test_share_folder_file_success(app_factory, tmp_path):
    stored = uuid.uuid4().hex
    user_dir = tmp_path / USER_ID
    user_dir.mkdir()
    (user_dir / stored).write_bytes(b"individual file")
    file_entry = _file_row(stored_name=stored, mime_type="text/plain")
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row(target_type="folder", file_id=None, folder_id=FOLDER_ID)],
    })
    ctx = _make_ctx(client, query_rows=[file_entry])
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/files/{FILE_ID}")
    assert resp.status_code == 200
    assert b"individual file" in resp.data
    assert "hello.txt" in resp.headers.get("Content-Disposition", "")


def test_share_folder_file_dangerous_mime_served_as_octet_stream(app_factory, tmp_path):
    stored = uuid.uuid4().hex
    user_dir = tmp_path / USER_ID
    user_dir.mkdir()
    (user_dir / stored).write_bytes(b"#!/bin/bash\nrm -rf /")
    file_entry = _file_row(stored_name=stored, mime_type="application/x-sh")
    client = _ShareClient(rows_map={
        "file_share_links": [_link_row(target_type="folder", file_id=None, folder_id=FOLDER_ID)],
    })
    ctx = _make_ctx(client, query_rows=[file_entry])
    app = _make_app(app_factory, ctx, tmp_path)
    resp = app.test_client().get(f"/api/share/{LINK_ID}/files/{FILE_ID}")
    assert resp.status_code == 200
    assert resp.content_type.startswith("application/octet-stream")
