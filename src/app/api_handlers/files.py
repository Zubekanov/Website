from __future__ import annotations

import logging
import mimetypes
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from urllib.parse import unquote
from datetime import datetime, timezone

import flask

from app.api_context import ApiContext
from app.api_common import get_request_user, require_admin

logger = logging.getLogger(__name__)

# Admin gets this effective quota (bytes) for display purposes.
ADMIN_DISPLAY_QUOTA = 100 * 1024 * 1024 * 1024  # 100 GB

# Maximum original filename length stored in the DB (sanity cap).
_MAX_FILENAME_LEN = 255

# Chunked upload settings.
_UPLOAD_ID_RE     = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_CHUNK_MAX_BYTES  = 2 * 1024 * 1024    # 2 MB hard cap per individual chunk
_UPLOAD_MAX_BYTES = 512 * 1024 * 1024  # 512 MB max assembled file
_STALE_UPLOAD_AGE = 86400              # 24 h — abandon incomplete uploads after this

# MIME types that must never be served as-is (browsers may execute them).
_DANGEROUS_MIME_TYPES = frozenset({
    "text/html",
    "application/javascript",
    "text/javascript",
    "application/x-sh",
    "text/x-sh",
    "application/x-httpd-php",
    "application/x-executable",
})


def _safe_mime_type(mime_type: str | None) -> str:
    """Return the MIME type, substituting octet-stream for any dangerous type."""
    if not mime_type:
        return "application/octet-stream"
    base = mime_type.split(";")[0].strip().lower()
    if base in _DANGEROUS_MIME_TYPES:
        return "application/octet-stream"
    return mime_type


def _upload_root() -> str:
    return flask.current_app.config["UPLOAD_FOLDER"]


def _user_dir(user_id: str) -> str:
    return os.path.join(_upload_root(), str(user_id))


def _file_path(user_id: str, stored_name: str) -> str:
    return os.path.join(_user_dir(user_id), stored_name)


def _safe_dest(user_id: str, stored_name: str) -> str:
    """Return the resolved absolute path and assert it stays within UPLOAD_FOLDER."""
    root = os.path.realpath(_upload_root())
    dest = os.path.realpath(os.path.join(_user_dir(user_id), stored_name))
    if not dest.startswith(root + os.sep):
        raise ValueError("Path traversal detected.")
    return dest


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _tmp_dir() -> str:
    return os.path.normpath(os.path.join(_upload_root(), "..", "uploads_tmp"))


def _tmp_path(upload_id: str) -> str:
    return os.path.join(_tmp_dir(), upload_id + ".part")


def _write_zip_to_tmp(disk_path: str, arcname: str) -> str:
    """Write a ZIP_STORED single-file zip into uploads_tmp; return its path.

    The caller must delete the path when the response has been sent.
    """
    tmp = _tmp_dir()
    os.makedirs(tmp, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip", dir=tmp)
    os.close(tmp_fd)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            zf.write(disk_path, arcname)
    except Exception:
        _unlink_safe(tmp_path)
        raise
    return tmp_path


def _unlink_safe(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _is_valid_zip(path: str) -> bool:
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


def _validate_upload_id(upload_id: str) -> bool:
    return bool(upload_id and _UPLOAD_ID_RE.match(upload_id))


def _cleanup_stale_uploads() -> None:
    """Delete .part and .zip temp files older than _STALE_UPLOAD_AGE. Best-effort, never raises."""
    try:
        tmp = _tmp_dir()
        if not os.path.isdir(tmp):
            return
        cutoff = time.time() - _STALE_UPLOAD_AGE
        for name in os.listdir(tmp):
            if not (name.endswith(".part") or name.endswith(".zip")):
                continue
            path = os.path.join(tmp, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass
    except Exception:
        pass


def register(api: flask.Blueprint, ctx: ApiContext) -> None:

    # ------------------------------------------------------------------
    # Member: quota status
    # ------------------------------------------------------------------

    @api.route("/api/files/quota", methods=["GET"])
    def api_files_quota():
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        is_admin = ctx.interface.is_admin(user_id)

        if is_admin:
            return flask.jsonify({
                "ok": True,
                "status": "approved",
                "quota_bytes": ADMIN_DISPLAY_QUOTA,
                "used_bytes": _admin_used_bytes(ctx, user_id),
                "is_admin": True,
            })

        rows, _ = ctx.interface.client.get_rows_with_filters(
            "user_storage_quotas",
            equalities={"user_id": user_id},
            page_limit=1,
            page_num=0,
        )
        if not rows:
            return flask.jsonify({"ok": True, "status": "none"})

        row = rows[0]
        return flask.jsonify({
            "ok": True,
            "status": row["status"],
            "quota_bytes": row["quota_bytes"],
            "used_bytes": row["used_bytes"],
            "admin_note": row.get("admin_note"),
            "is_admin": False,
        })

    # ------------------------------------------------------------------
    # Member: request quota
    # ------------------------------------------------------------------

    @api.route("/api/files/quota/request", methods=["POST"])
    def api_files_quota_request():
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        if ctx.interface.is_admin(user_id):
            return flask.jsonify({"ok": False, "message": "Admins have unlimited quota."}), 400

        data = flask.request.json or {}
        quota_bytes = data.get("quota_bytes")
        note = (data.get("note") or "").strip()

        if not isinstance(quota_bytes, int) or quota_bytes <= 0:
            return flask.jsonify({"ok": False, "message": "Invalid quota size."}), 400
        if not note:
            return flask.jsonify({"ok": False, "message": "Please provide a reason for your request."}), 400

        # Cap individual request at 50 GB to prevent typos.
        max_request = 50 * 1024 * 1024 * 1024
        if quota_bytes > max_request:
            return flask.jsonify({"ok": False, "message": "Maximum requestable quota is 50 GB."}), 400

        rows, _ = ctx.interface.client.get_rows_with_filters(
            "user_storage_quotas",
            equalities={"user_id": user_id},
            page_limit=1,
            page_num=0,
        )

        now = datetime.now(timezone.utc)

        if rows:
            existing = rows[0]
            if existing["status"] == "approved":
                return flask.jsonify({"ok": False, "message": "You already have an active quota."}), 400
            # Re-request: update in place.
            ctx.interface.client.update_rows_with_filters(
                "user_storage_quotas",
                {
                    "quota_bytes": quota_bytes,
                    "status": "pending",
                    "request_note": note,
                    "admin_note": None,
                    "requested_at": now,
                    "approved_at": None,
                },
                equalities={"user_id": user_id},
            )
        else:
            ctx.interface.client.insert_row("user_storage_quotas", {
                "user_id": user_id,
                "quota_bytes": quota_bytes,
                "used_bytes": 0,
                "status": "pending",
                "request_note": note,
                "requested_at": now,
            })

        return flask.jsonify({"ok": True, "message": "Quota request submitted."})

    # ------------------------------------------------------------------
    # Member: list files
    # ------------------------------------------------------------------

    @api.route("/api/files/list", methods=["GET"])
    def api_files_list():
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        rows, _ = ctx.interface.client.get_rows_with_filters(
            "user_files",
            equalities={"user_id": user_id},
            page_limit=200,
            page_num=0,
            order_by="created_at",
            order_dir="DESC",
        )

        files = [_serialize_file(r) for r in rows]
        return flask.jsonify({"ok": True, "files": files})

    # ------------------------------------------------------------------
    # Member: upload
    # ------------------------------------------------------------------

    @api.route("/api/files/upload", methods=["POST"])
    def api_files_upload():
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        is_admin = ctx.interface.is_admin(user_id)

        if not is_admin:
            # Verify approved quota with headroom.
            rows, _ = ctx.interface.client.get_rows_with_filters(
                "user_storage_quotas",
                equalities={"user_id": user_id},
                page_limit=1,
                page_num=0,
            )
            if not rows or rows[0]["status"] != "approved":
                return flask.jsonify({"ok": False, "message": "You do not have an approved storage quota."}), 403

            quota_row = rows[0]
            available = quota_row["quota_bytes"] - quota_row["used_bytes"]
            if available <= 0:
                return flask.jsonify({"ok": False, "message": "Storage quota full."}), 400
        else:
            quota_row = None
            available = None

        raw_name = flask.request.headers.get("X-Filename", "").strip()
        if not raw_name:
            return flask.jsonify({"ok": False, "message": "Filename is empty."}), 400
        original_name = unquote(raw_name)[:_MAX_FILENAME_LEN].strip()
        if not original_name:
            return flask.jsonify({"ok": False, "message": "Filename is empty."}), 400

        # Detect MIME type from filename.
        guessed, _ = mimetypes.guess_type(original_name)
        mime_type = _safe_mime_type(guessed)

        # Generate stored name (UUID hex, no extension).
        stored_name = uuid.uuid4().hex

        # Stream file to disk in chunks; enforce quota limit during write.
        dest = None
        size_bytes = 0
        quota_exceeded = False
        try:
            dest = _safe_dest(user_id, stored_name)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            limit = available  # None for admins
            with open(dest, "wb") as fh:
                for chunk in iter(lambda: flask.request.stream.read(65536), b""):
                    size_bytes += len(chunk)
                    if limit is not None and size_bytes > limit:
                        quota_exceeded = True
                        break
                    fh.write(chunk)
        except Exception:
            logger.exception("Failed to write uploaded file for user %s", user_id)
            if dest and os.path.isfile(dest):
                try:
                    os.remove(dest)
                except OSError:
                    pass
            return flask.jsonify({"ok": False, "message": "Failed to save file. Please try again."}), 500

        if size_bytes == 0 or quota_exceeded:
            if dest and os.path.isfile(dest):
                try:
                    os.remove(dest)
                except OSError:
                    pass
            if size_bytes == 0:
                return flask.jsonify({"ok": False, "message": "Empty files are not allowed."}), 400
            return flask.jsonify({
                "ok": False,
                "message": f"File is too large. Available: {_fmt_bytes(available)}.",
            }), 400

        # Insert DB record.
        try:
            now = datetime.now(timezone.utc)
            file_row = ctx.interface.client.insert_row("user_files", {
                "user_id": user_id,
                "original_name": original_name,
                "stored_name": stored_name,
                "mime_type": mime_type,
                "size_bytes": size_bytes,
                "download_count": 0,
                "created_at": now,
                "updated_at": now,
            })
        except Exception:
            logger.exception("Failed to insert file record for user %s", user_id)
            try:
                os.remove(dest)
            except OSError:
                pass
            return flask.jsonify({"ok": False, "message": "Failed to record file. Please try again."}), 500

        # Update used_bytes — failure rolls back the upload to keep quota accurate.
        if not is_admin and quota_row is not None:
            try:
                ctx.interface.client.update_rows_with_filters(
                    "user_storage_quotas",
                    {"used_bytes": quota_row["used_bytes"] + size_bytes},
                    equalities={"user_id": user_id},
                )
            except Exception:
                logger.exception("Failed to update used_bytes for user %s — rolling back upload", user_id)
                try:
                    ctx.interface.client.delete_rows_with_filters(
                        "user_files", equalities={"id": str(file_row["id"])})
                except Exception:
                    logger.exception("Failed to delete file record during quota rollback for user %s", user_id)
                if dest and os.path.isfile(dest):
                    try:
                        os.remove(dest)
                    except OSError:
                        pass
                return flask.jsonify({"ok": False, "message": "Failed to record storage usage. Please try again."}), 500

        return flask.jsonify({"ok": True, "file": _serialize_file(file_row)})

    # ------------------------------------------------------------------
    # Member: upload progress polling
    # ------------------------------------------------------------------

    @api.route("/api/files/upload/progress/<upload_id>", methods=["GET"])
    def api_files_upload_progress(upload_id: str):
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        if not _validate_upload_id(upload_id):
            return flask.jsonify({"ok": False, "message": "Invalid upload ID."}), 400

        part = _tmp_path(upload_id)
        try:
            bytes_written = os.path.getsize(part) if os.path.isfile(part) else 0
        except OSError:
            bytes_written = 0

        return flask.jsonify({"ok": True, "bytes_written": bytes_written})

    # ------------------------------------------------------------------
    # Member: chunked upload — receive one chunk
    # ------------------------------------------------------------------

    @api.route("/api/files/upload/chunk", methods=["POST"])
    def api_files_upload_chunk():
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        upload_id = (flask.request.headers.get("X-Upload-Id") or "").strip()
        if not _validate_upload_id(upload_id):
            return flask.jsonify({"ok": False, "message": "Invalid upload ID."}), 400

        try:
            chunk_index  = int(flask.request.headers.get("X-Chunk-Index",  -1))
            total_chunks = int(flask.request.headers.get("X-Total-Chunks",  0))
            total_size   = int(flask.request.headers.get("X-Total-Size",    0))
        except (ValueError, TypeError):
            return flask.jsonify({"ok": False, "message": "Invalid chunk parameters."}), 400

        filename = unquote((flask.request.headers.get("X-Filename") or "").strip())
        if not filename:
            return flask.jsonify({"ok": False, "message": "Filename required."}), 400

        if chunk_index < 0 or total_chunks <= 0 or chunk_index >= total_chunks:
            return flask.jsonify({"ok": False, "message": "Invalid chunk index."}), 400

        if total_size <= 0 or total_size > _UPLOAD_MAX_BYTES:
            return flask.jsonify({"ok": False, "message": f"Total size out of range (max {_fmt_bytes(_UPLOAD_MAX_BYTES)})."}), 400

        user_id  = str(user["id"])
        is_admin = ctx.interface.is_admin(user_id)

        if chunk_index == 0:
            # Validate quota upfront on the first chunk.
            if not is_admin:
                rows, _ = ctx.interface.client.get_rows_with_filters(
                    "user_storage_quotas",
                    equalities={"user_id": user_id},
                    page_limit=1,
                    page_num=0,
                )
                if not rows or rows[0]["status"] != "approved":
                    return flask.jsonify({"ok": False, "message": "You do not have an approved storage quota."}), 403
                available = int(rows[0]["quota_bytes"]) - int(rows[0]["used_bytes"])
                if total_size > available:
                    return flask.jsonify({"ok": False, "message": f"File too large. Available: {_fmt_bytes(available)}."}), 400
            _cleanup_stale_uploads()

        try:
            tmp = _tmp_dir()
            os.makedirs(tmp, exist_ok=True)
            part = _tmp_path(upload_id)
            mode = "wb" if chunk_index == 0 else "ab"
            with open(part, mode) as fh:
                for data in iter(lambda: flask.request.stream.read(65536), b""):
                    fh.write(data)
        except Exception:
            logger.exception("Failed to write chunk %d for upload %s (user %s)", chunk_index, upload_id, user_id)
            return flask.jsonify({"ok": False, "message": "Failed to write chunk."}), 500

        return flask.jsonify({"ok": True})

    # ------------------------------------------------------------------
    # Member: chunked upload — finalise assembled file
    # ------------------------------------------------------------------

    @api.route("/api/files/upload/complete", methods=["POST"])
    def api_files_upload_complete():
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        data = flask.request.json or {}
        upload_id = (data.get("upload_id") or "").strip()
        if not _validate_upload_id(upload_id):
            return flask.jsonify({"ok": False, "message": "Invalid upload ID."}), 400

        filename = (data.get("filename") or "").strip()[:_MAX_FILENAME_LEN]
        if not filename:
            return flask.jsonify({"ok": False, "message": "Filename required."}), 400

        try:
            total_size = int(data.get("total_size", 0))
        except (ValueError, TypeError):
            return flask.jsonify({"ok": False, "message": "Invalid total_size."}), 400

        part = _tmp_path(upload_id)
        if not os.path.isfile(part):
            return flask.jsonify({"ok": False, "message": "Upload session not found or expired."}), 404

        actual_size = os.path.getsize(part)
        if actual_size != total_size:
            try:
                os.remove(part)
            except OSError:
                pass
            return flask.jsonify({"ok": False, "message": "Upload incomplete or corrupted — please retry."}), 400

        user_id  = str(user["id"])
        is_admin = ctx.interface.is_admin(user_id)

        # Re-validate quota (may have changed during a long upload).
        if not is_admin:
            rows, _ = ctx.interface.client.get_rows_with_filters(
                "user_storage_quotas",
                equalities={"user_id": user_id},
                page_limit=1,
                page_num=0,
            )
            if not rows or rows[0]["status"] != "approved":
                try:
                    os.remove(part)
                except OSError:
                    pass
                return flask.jsonify({"ok": False, "message": "You do not have an approved storage quota."}), 403
            quota_row = rows[0]
            available = int(quota_row["quota_bytes"]) - int(quota_row["used_bytes"])
            if actual_size > available:
                try:
                    os.remove(part)
                except OSError:
                    pass
                return flask.jsonify({"ok": False, "message": f"File too large. Available: {_fmt_bytes(available)}."}), 400
        else:
            quota_row = None

        mime_type, _ = mimetypes.guess_type(filename)
        mime_type = _safe_mime_type(mime_type)
        stored_name = uuid.uuid4().hex

        try:
            dest = _safe_dest(user_id, stored_name)
        except ValueError:
            try:
                os.remove(part)
            except OSError:
                pass
            return flask.jsonify({"ok": False, "message": "Internal path error."}), 500

        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            os.rename(part, dest)
        except OSError:
            try:
                shutil.move(part, dest)
            except Exception:
                logger.exception("Failed to move temp file to final dest for user %s", user_id)
                try:
                    os.remove(part)
                except OSError:
                    pass
                return flask.jsonify({"ok": False, "message": "Failed to save file. Please try again."}), 500

        try:
            now = datetime.now(timezone.utc)
            file_row = ctx.interface.client.insert_row("user_files", {
                "user_id": user_id,
                "original_name": filename,
                "stored_name": stored_name,
                "mime_type": mime_type,
                "size_bytes": actual_size,
                "download_count": 0,
                "created_at": now,
                "updated_at": now,
            })
        except Exception:
            logger.exception("Failed to insert file record for user %s", user_id)
            try:
                os.remove(dest)
            except OSError:
                pass
            return flask.jsonify({"ok": False, "message": "Failed to record file. Please try again."}), 500

        if not is_admin and quota_row is not None:
            try:
                ctx.interface.client.update_rows_with_filters(
                    "user_storage_quotas",
                    {"used_bytes": int(quota_row["used_bytes"]) + actual_size},
                    equalities={"user_id": user_id},
                )
            except Exception:
                logger.exception("Failed to update used_bytes for user %s — rolling back", user_id)
                try:
                    ctx.interface.client.delete_rows_with_filters("user_files", equalities={"id": str(file_row["id"])})
                except Exception:
                    logger.exception("Failed to delete file record during quota rollback for user %s", user_id)
                try:
                    os.remove(dest)
                except OSError:
                    pass
                return flask.jsonify({"ok": False, "message": "Failed to record storage usage. Please try again."}), 500

        return flask.jsonify({"ok": True, "file": _serialize_file(file_row)})

    # ------------------------------------------------------------------
    # Member: download
    # ------------------------------------------------------------------

    @api.route("/api/files/download/<file_id>", methods=["GET"])
    def api_files_download(file_id: str):
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        is_admin = ctx.interface.is_admin(user_id)

        rows, _ = ctx.interface.client.get_rows_with_filters(
            "user_files",
            equalities={"id": file_id},
            page_limit=1,
            page_num=0,
        )
        if not rows:
            return flask.jsonify({"ok": False, "message": "File not found."}), 404

        row = rows[0]
        owner_id = str(row["user_id"])

        if not is_admin and owner_id != user_id:
            return flask.jsonify({"ok": False, "message": "Access denied."}), 403

        try:
            path = _safe_dest(owner_id, row["stored_name"])
        except ValueError:
            logger.error("Path traversal attempt on file %s by user %s", file_id, user_id)
            return flask.jsonify({"ok": False, "message": "File not found on disk."}), 404

        if not os.path.isfile(path):
            return flask.jsonify({"ok": False, "message": "File not found on disk."}), 404

        # Increment download count (best-effort).
        try:
            ctx.interface.client.update_rows_with_filters(
                "user_files",
                {"download_count": int(row["download_count"] or 0) + 1,
                 "updated_at": datetime.now(timezone.utc)},
                equalities={"id": file_id},
            )
        except Exception:
            logger.warning("Failed to increment download_count for file %s", file_id)

        return flask.send_file(
            path,
            as_attachment=True,
            download_name=row["original_name"],
            mimetype=_safe_mime_type(row.get("mime_type")),
        )

    # ------------------------------------------------------------------
    # Member: download own file as ZIP
    # ------------------------------------------------------------------

    @api.route("/api/files/download/<file_id>/zip", methods=["GET"])
    def api_files_download_zip(file_id: str):
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        is_admin = ctx.interface.is_admin(user_id)

        rows, _ = ctx.interface.client.get_rows_with_filters(
            "user_files",
            equalities={"id": file_id},
            page_limit=1,
            page_num=0,
        )
        if not rows:
            return flask.jsonify({"ok": False, "message": "File not found."}), 404

        row = rows[0]
        if not is_admin and str(row["user_id"]) != user_id:
            return flask.jsonify({"ok": False, "message": "Access denied."}), 403

        try:
            path = _safe_dest(user_id, row["stored_name"])
        except ValueError:
            logger.error("Path traversal attempt on file %s by user %s", file_id, user_id)
            return flask.jsonify({"ok": False, "message": "File not found on disk."}), 404

        if not os.path.isfile(path):
            return flask.jsonify({"ok": False, "message": "File not found on disk."}), 404

        # Increment download count (best-effort).
        try:
            ctx.interface.client.update_rows_with_filters(
                "user_files",
                {"download_count": int(row["download_count"] or 0) + 1,
                 "updated_at": datetime.now(timezone.utc)},
                equalities={"id": file_id},
            )
        except Exception:
            logger.warning("Failed to increment download_count for file %s", file_id)

        # Already a valid zip — serve as-is.
        if row["original_name"].lower().endswith(".zip") and _is_valid_zip(path):
            return flask.send_file(
                path,
                mimetype="application/zip",
                as_attachment=True,
                download_name=row["original_name"],
            )

        safe_name = (row["original_name"] or "file").replace("/", "_").replace("\\", "_")
        try:
            tmp_path = _write_zip_to_tmp(path, safe_name)
        except Exception:
            logger.exception("Failed to build zip for file %s", file_id)
            return flask.jsonify({"ok": False, "message": "Failed to create ZIP."}), 500

        response = flask.send_file(
            tmp_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{safe_name}.zip",
        )
        response.call_on_close(lambda: _unlink_safe(tmp_path))
        return response

    # ------------------------------------------------------------------
    # Member: rename own file
    # ------------------------------------------------------------------

    @api.route("/api/files/<file_id>", methods=["PATCH"])
    def api_files_rename(file_id: str):
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        data = flask.request.json or {}
        new_name = (data.get("name") or "").strip()[:_MAX_FILENAME_LEN]
        if not new_name:
            return flask.jsonify({"ok": False, "message": "Name cannot be empty."}), 400

        user_id = str(user["id"])
        is_admin = ctx.interface.is_admin(user_id)

        rows, _ = ctx.interface.client.get_rows_with_filters(
            "user_files",
            equalities={"id": file_id},
            page_limit=1,
            page_num=0,
        )
        if not rows:
            return flask.jsonify({"ok": False, "message": "File not found."}), 404
        if not is_admin and str(rows[0]["user_id"]) != user_id:
            return flask.jsonify({"ok": False, "message": "Access denied."}), 403

        ctx.interface.client.update_rows_with_filters(
            "user_files",
            {"original_name": new_name, "updated_at": datetime.now(timezone.utc)},
            equalities={"id": file_id},
        )
        return flask.jsonify({"ok": True, "name": new_name})

    # ------------------------------------------------------------------
    # Member: delete own file
    # ------------------------------------------------------------------

    @api.route("/api/files/<file_id>", methods=["DELETE"])
    def api_files_delete(file_id: str):
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        is_admin = ctx.interface.is_admin(user_id)

        rows, _ = ctx.interface.client.get_rows_with_filters(
            "user_files",
            equalities={"id": file_id},
            page_limit=1,
            page_num=0,
        )
        if not rows:
            return flask.jsonify({"ok": False, "message": "File not found."}), 404

        row = rows[0]
        owner_id = str(row["user_id"])

        if not is_admin and owner_id != user_id:
            return flask.jsonify({"ok": False, "message": "Access denied."}), 403

        _delete_file_record(ctx, row)
        return flask.jsonify({"ok": True, "message": "File deleted."})

    # ------------------------------------------------------------------
    # Admin: list quota records
    # ------------------------------------------------------------------

    @api.route("/api/admin/files/quota/list", methods=["GET"])
    def api_admin_files_quota_list():
        user, err = require_admin(ctx)
        if err:
            return err

        rows = ctx.interface.execute_query(
            """
            SELECT q.user_id, q.quota_bytes, q.used_bytes, q.status,
                   q.request_note, q.admin_note, q.requested_at, q.approved_at,
                   u.first_name, u.last_name, u.email
            FROM user_storage_quotas q
            JOIN users u ON q.user_id = u.id
            ORDER BY
                CASE q.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                q.requested_at ASC;
            """
        )

        result = []
        for r in (rows or []):
            result.append({
                "user_id": str(r["user_id"]),
                "first_name": r.get("first_name"),
                "last_name": r.get("last_name"),
                "email": r.get("email"),
                "quota_bytes": r["quota_bytes"],
                "used_bytes": r["used_bytes"],
                "status": r["status"],
                "request_note": r.get("request_note"),
                "admin_note": r.get("admin_note"),
                "requested_at": _iso(r.get("requested_at")),
                "approved_at": _iso(r.get("approved_at")),
            })

        return flask.jsonify({"ok": True, "quotas": result})

    # ------------------------------------------------------------------
    # Admin: set/approve/deny quota
    # ------------------------------------------------------------------

    @api.route("/api/admin/files/quota/set", methods=["POST"])
    def api_admin_files_quota_set():
        user, err = require_admin(ctx)
        if err:
            return err

        data = flask.request.json or {}
        target_user_id = (data.get("user_id") or "").strip()
        status = (data.get("status") or "").strip()
        admin_note = (data.get("admin_note") or "").strip() or None
        quota_bytes = data.get("quota_bytes")

        if not target_user_id:
            return flask.jsonify({"ok": False, "message": "Missing user_id."}), 400
        if status not in ("approved", "denied"):
            return flask.jsonify({"ok": False, "message": "status must be 'approved' or 'denied'."}), 400
        if status == "approved":
            if not isinstance(quota_bytes, int) or quota_bytes <= 0:
                return flask.jsonify({"ok": False, "message": "Invalid quota_bytes for approval."}), 400

        # Ensure the target user exists.
        target_rows, _ = ctx.interface.client.get_rows_with_filters(
            "users",
            equalities={"id": target_user_id},
            page_limit=1,
            page_num=0,
        )
        if not target_rows:
            return flask.jsonify({"ok": False, "message": "User not found."}), 404

        quota_rows, _ = ctx.interface.client.get_rows_with_filters(
            "user_storage_quotas",
            equalities={"user_id": target_user_id},
            page_limit=1,
            page_num=0,
        )

        now = datetime.now(timezone.utc)
        updates: dict = {
            "status": status,
            "admin_note": admin_note,
        }
        if status == "approved":
            updates["quota_bytes"] = quota_bytes
            updates["approved_at"] = now

        if quota_rows:
            ctx.interface.client.update_rows_with_filters(
                "user_storage_quotas",
                updates,
                equalities={"user_id": target_user_id},
            )
        else:
            # Admin is setting quota for a user who never requested.
            ctx.interface.client.insert_row("user_storage_quotas", {
                "user_id": target_user_id,
                "quota_bytes": quota_bytes or 0,
                "used_bytes": 0,
                "status": status,
                "admin_note": admin_note,
                "requested_at": now,
                "approved_at": now if status == "approved" else None,
            })

        return flask.jsonify({"ok": True, "message": f"Quota {status}."})

    # ------------------------------------------------------------------
    # Admin: list all files
    # ------------------------------------------------------------------

    @api.route("/api/admin/files/list", methods=["GET"])
    def api_admin_files_list():
        user, err = require_admin(ctx)
        if err:
            return err

        filter_user_id = (flask.request.args.get("user_id") or "").strip() or None

        if filter_user_id:
            rows = ctx.interface.execute_query(
                """
                SELECT f.id, f.user_id, f.original_name, f.stored_name,
                       f.mime_type, f.size_bytes, f.download_count, f.created_at,
                       u.first_name, u.last_name, u.email
                FROM user_files f
                JOIN users u ON f.user_id = u.id
                WHERE f.user_id = %s
                ORDER BY f.created_at DESC;
                """,
                (filter_user_id,),
            )
        else:
            rows = ctx.interface.execute_query(
                """
                SELECT f.id, f.user_id, f.original_name, f.stored_name,
                       f.mime_type, f.size_bytes, f.download_count, f.created_at,
                       u.first_name, u.last_name, u.email
                FROM user_files f
                JOIN users u ON f.user_id = u.id
                ORDER BY f.created_at DESC
                LIMIT 500;
                """
            )

        result = []
        total_bytes = 0
        for r in (rows or []):
            total_bytes += int(r.get("size_bytes") or 0)
            result.append({
                "id": str(r["id"]),
                "user_id": str(r["user_id"]),
                "first_name": r.get("first_name"),
                "last_name": r.get("last_name"),
                "email": r.get("email"),
                "original_name": r["original_name"],
                "mime_type": r.get("mime_type"),
                "size_bytes": r["size_bytes"],
                "download_count": r["download_count"],
                "created_at": _iso(r.get("created_at")),
            })

        return flask.jsonify({
            "ok": True,
            "files": result,
            "total_bytes": total_bytes,
            "total_files": len(result),
        })

    # ------------------------------------------------------------------
    # Admin: delete any file
    # ------------------------------------------------------------------

    @api.route("/api/admin/files/<file_id>", methods=["DELETE"])
    def api_admin_files_delete(file_id: str):
        user, err = require_admin(ctx)
        if err:
            return err

        rows, _ = ctx.interface.client.get_rows_with_filters(
            "user_files",
            equalities={"id": file_id},
            page_limit=1,
            page_num=0,
        )
        if not rows:
            return flask.jsonify({"ok": False, "message": "File not found."}), 404

        _delete_file_record(ctx, rows[0])
        return flask.jsonify({"ok": True, "message": "File deleted."})

    # ------------------------------------------------------------------
    # Member: list folders
    # ------------------------------------------------------------------

    @api.route("/api/files/folders", methods=["GET"])
    def api_files_folders_list():
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        rows = ctx.interface.execute_query(
            """
            SELECT f.id, f.name, f.created_at, f.updated_at,
                   COUNT(fi.id) AS file_count
            FROM file_folders f
            LEFT JOIN file_folder_items fi ON fi.folder_id = f.id
            WHERE f.user_id = %s
            GROUP BY f.id, f.name, f.created_at, f.updated_at
            ORDER BY f.name ASC;
            """,
            (user_id,),
        )
        folders = [_serialize_folder(r) for r in (rows or [])]
        return flask.jsonify({"ok": True, "folders": folders})

    # ------------------------------------------------------------------
    # Member: create folder
    # ------------------------------------------------------------------

    @api.route("/api/files/folders", methods=["POST"])
    def api_files_folders_create():
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        data = flask.request.json or {}
        name = (data.get("name") or "").strip()[:255]
        if not name:
            return flask.jsonify({"ok": False, "message": "Folder name is required."}), 400

        now = datetime.now(timezone.utc)
        row = ctx.interface.client.insert_row("file_folders", {
            "user_id": user_id,
            "name": name,
            "created_at": now,
            "updated_at": now,
        })
        return flask.jsonify({"ok": True, "folder": _serialize_folder(row)})

    # ------------------------------------------------------------------
    # Member: delete folder (files remain)
    # ------------------------------------------------------------------

    @api.route("/api/files/folders/<folder_id>", methods=["DELETE"])
    def api_files_folders_delete(folder_id: str):
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        rows, _ = ctx.interface.client.get_rows_with_filters(
            "file_folders",
            equalities={"id": folder_id},
            page_limit=1, page_num=0,
        )
        if not rows:
            return flask.jsonify({"ok": False, "message": "Folder not found."}), 404
        if str(rows[0]["user_id"]) != user_id and not ctx.interface.is_admin(user_id):
            return flask.jsonify({"ok": False, "message": "Access denied."}), 403

        ctx.interface.client.delete_rows_with_filters("file_folders", equalities={"id": folder_id})
        return flask.jsonify({"ok": True, "message": "Folder deleted."})

    # ------------------------------------------------------------------
    # Member: list files in a folder
    # ------------------------------------------------------------------

    @api.route("/api/files/folders/<folder_id>", methods=["GET"])
    def api_files_folder_contents(folder_id: str):
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        folder_rows, _ = ctx.interface.client.get_rows_with_filters(
            "file_folders",
            equalities={"id": folder_id},
            page_limit=1, page_num=0,
        )
        if not folder_rows:
            return flask.jsonify({"ok": False, "message": "Folder not found."}), 404
        if str(folder_rows[0]["user_id"]) != user_id and not ctx.interface.is_admin(user_id):
            return flask.jsonify({"ok": False, "message": "Access denied."}), 403

        rows = ctx.interface.execute_query(
            """
            SELECT uf.id, uf.original_name, uf.mime_type, uf.size_bytes,
                   uf.download_count, uf.created_at, fi.added_at
            FROM file_folder_items fi
            JOIN user_files uf ON fi.file_id = uf.id
            WHERE fi.folder_id = %s
            ORDER BY fi.added_at ASC;
            """,
            (folder_id,),
        )
        files = []
        for r in (rows or []):
            f = _serialize_file(r)
            f["added_at"] = _iso(r.get("added_at"))
            files.append(f)
        return flask.jsonify({"ok": True, "files": files})

    # ------------------------------------------------------------------
    # Member: add file to folder
    # ------------------------------------------------------------------

    @api.route("/api/files/folders/<folder_id>/items", methods=["POST"])
    def api_files_folder_add_item(folder_id: str):
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        folder_rows, _ = ctx.interface.client.get_rows_with_filters(
            "file_folders",
            equalities={"id": folder_id},
            page_limit=1, page_num=0,
        )
        if not folder_rows:
            return flask.jsonify({"ok": False, "message": "Folder not found."}), 404
        if str(folder_rows[0]["user_id"]) != user_id:
            return flask.jsonify({"ok": False, "message": "Access denied."}), 403

        data = flask.request.json or {}
        file_id = (data.get("file_id") or "").strip()
        if not file_id:
            return flask.jsonify({"ok": False, "message": "file_id is required."}), 400

        file_rows, _ = ctx.interface.client.get_rows_with_filters(
            "user_files",
            equalities={"id": file_id, "user_id": user_id},
            page_limit=1, page_num=0,
        )
        if not file_rows:
            return flask.jsonify({"ok": False, "message": "File not found."}), 404

        # Check not already in folder.
        existing, _ = ctx.interface.client.get_rows_with_filters(
            "file_folder_items",
            equalities={"folder_id": folder_id, "file_id": file_id},
            page_limit=1, page_num=0,
        )
        if existing:
            return flask.jsonify({"ok": True, "message": "File already in folder."})

        ctx.interface.client.insert_row("file_folder_items", {
            "folder_id": folder_id,
            "file_id": file_id,
            "added_at": datetime.now(timezone.utc),
        })
        return flask.jsonify({"ok": True, "message": "File added to folder."})

    # ------------------------------------------------------------------
    # Member: remove file from folder
    # ------------------------------------------------------------------

    @api.route("/api/files/folders/<folder_id>/items/<file_id>", methods=["DELETE"])
    def api_files_folder_remove_item(folder_id: str, file_id: str):
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        folder_rows, _ = ctx.interface.client.get_rows_with_filters(
            "file_folders",
            equalities={"id": folder_id},
            page_limit=1, page_num=0,
        )
        if not folder_rows:
            return flask.jsonify({"ok": False, "message": "Folder not found."}), 404
        if str(folder_rows[0]["user_id"]) != user_id:
            return flask.jsonify({"ok": False, "message": "Access denied."}), 403

        ctx.interface.client.delete_rows_with_filters(
            "file_folder_items",
            equalities={"folder_id": folder_id, "file_id": file_id},
        )
        return flask.jsonify({"ok": True, "message": "File removed from folder."})

    # ------------------------------------------------------------------
    # Member: create share link
    # ------------------------------------------------------------------

    @api.route("/api/files/share", methods=["POST"])
    def api_files_share_create():
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        data = flask.request.json or {}
        target_type = (data.get("target_type") or "").strip()
        target_id = (data.get("target_id") or "").strip()

        if target_type not in ("file", "folder"):
            return flask.jsonify({"ok": False, "message": "target_type must be 'file' or 'folder'."}), 400
        if not target_id:
            return flask.jsonify({"ok": False, "message": "target_id is required."}), 400

        if target_type == "file":
            rows, _ = ctx.interface.client.get_rows_with_filters(
                "user_files",
                equalities={"id": target_id, "user_id": user_id},
                page_limit=1, page_num=0,
            )
            if not rows:
                return flask.jsonify({"ok": False, "message": "File not found."}), 404
            insert = {"file_id": target_id, "folder_id": None}
            existing_filter = {"created_by": user_id, "file_id": target_id}
        else:
            rows, _ = ctx.interface.client.get_rows_with_filters(
                "file_folders",
                equalities={"id": target_id, "user_id": user_id},
                page_limit=1, page_num=0,
            )
            if not rows:
                return flask.jsonify({"ok": False, "message": "Folder not found."}), 404
            insert = {"file_id": None, "folder_id": target_id}
            existing_filter = {"created_by": user_id, "folder_id": target_id}

        # Return the existing link if one already exists for this user + target.
        existing, _ = ctx.interface.client.get_rows_with_filters(
            "file_share_links",
            equalities=existing_filter,
            page_limit=1, page_num=0,
        )
        if existing:
            link_id = str(existing[0]["id"])
            return flask.jsonify({
                "ok": True,
                "link_id": link_id,
                "url": f"/share/{link_id}",
                "existing": True,
            })

        now = datetime.now(timezone.utc)
        link_row = ctx.interface.client.insert_row("file_share_links", {
            "created_by": user_id,
            "target_type": target_type,
            "file_id": insert["file_id"],
            "folder_id": insert["folder_id"],
            "is_enabled": True,
            "download_count": 0,
            "created_at": now,
        })
        link_id = str(link_row["id"])
        return flask.jsonify({
            "ok": True,
            "link_id": link_id,
            "url": f"/share/{link_id}",
            "existing": False,
        })

    # ------------------------------------------------------------------
    # Member: list own share links
    # ------------------------------------------------------------------

    @api.route("/api/files/share", methods=["GET"])
    def api_files_share_list():
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        rows = ctx.interface.execute_query(
            """
            SELECT sl.id, sl.target_type, sl.is_enabled, sl.download_count,
                   sl.created_at, sl.last_accessed_at,
                   uf.original_name AS file_name, uf.size_bytes AS file_size,
                   ff.name AS folder_name
            FROM file_share_links sl
            LEFT JOIN user_files uf ON sl.file_id = uf.id
            LEFT JOIN file_folders ff ON sl.folder_id = ff.id
            WHERE sl.created_by = %s
            ORDER BY sl.created_at DESC;
            """,
            (user_id,),
        )
        links = [_serialize_share_link(r) for r in (rows or [])]
        return flask.jsonify({"ok": True, "links": links})

    # ------------------------------------------------------------------
    # Member: toggle or update share link
    # ------------------------------------------------------------------

    @api.route("/api/files/share/<link_id>", methods=["PATCH"])
    def api_files_share_update(link_id: str):
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        rows, _ = ctx.interface.client.get_rows_with_filters(
            "file_share_links",
            equalities={"id": link_id},
            page_limit=1, page_num=0,
        )
        if not rows:
            return flask.jsonify({"ok": False, "message": "Share link not found."}), 404
        if str(rows[0]["created_by"]) != user_id:
            return flask.jsonify({"ok": False, "message": "Access denied."}), 403

        data = flask.request.json or {}
        if "is_enabled" not in data:
            return flask.jsonify({"ok": False, "message": "is_enabled is required."}), 400

        ctx.interface.client.update_rows_with_filters(
            "file_share_links",
            {"is_enabled": bool(data["is_enabled"])},
            equalities={"id": link_id},
        )
        return flask.jsonify({"ok": True, "message": "Share link updated."})

    # ------------------------------------------------------------------
    # Member: delete share link
    # ------------------------------------------------------------------

    @api.route("/api/files/share/<link_id>", methods=["DELETE"])
    def api_files_share_delete(link_id: str):
        user = get_request_user(ctx)
        if not user:
            return flask.jsonify({"ok": False, "message": "Authentication required."}), 401

        user_id = str(user["id"])
        rows, _ = ctx.interface.client.get_rows_with_filters(
            "file_share_links",
            equalities={"id": link_id},
            page_limit=1, page_num=0,
        )
        if not rows:
            return flask.jsonify({"ok": False, "message": "Share link not found."}), 404
        is_admin = ctx.interface.is_admin(user_id)
        if str(rows[0]["created_by"]) != user_id and not is_admin:
            return flask.jsonify({"ok": False, "message": "Access denied."}), 403

        ctx.interface.client.delete_rows_with_filters("file_share_links", equalities={"id": link_id})
        return flask.jsonify({"ok": True, "message": "Share link deleted."})

    # ------------------------------------------------------------------
    # Admin: list all share links
    # ------------------------------------------------------------------

    @api.route("/api/admin/share/list", methods=["GET"])
    def api_admin_share_list():
        user, err = require_admin(ctx)
        if err:
            return err

        rows = ctx.interface.execute_query(
            """
            SELECT sl.id, sl.target_type, sl.is_enabled, sl.download_count,
                   sl.created_at, sl.last_accessed_at,
                   u.first_name, u.last_name, u.email,
                   uf.original_name AS file_name, uf.size_bytes AS file_size,
                   ff.name AS folder_name
            FROM file_share_links sl
            JOIN users u ON sl.created_by = u.id
            LEFT JOIN user_files uf ON sl.file_id = uf.id
            LEFT JOIN file_folders ff ON sl.folder_id = ff.id
            ORDER BY sl.created_at DESC
            LIMIT 500;
            """
        )
        links = []
        for r in (rows or []):
            link = _serialize_share_link(r)
            link["owner_first_name"] = r.get("first_name")
            link["owner_last_name"] = r.get("last_name")
            link["owner_email"] = r.get("email")
            links.append(link)
        return flask.jsonify({"ok": True, "links": links})

    # ------------------------------------------------------------------
    # Admin: toggle any share link
    # ------------------------------------------------------------------

    @api.route("/api/admin/share/<link_id>", methods=["PATCH"])
    def api_admin_share_update(link_id: str):
        user, err = require_admin(ctx)
        if err:
            return err

        data = flask.request.json or {}
        if "is_enabled" not in data:
            return flask.jsonify({"ok": False, "message": "is_enabled is required."}), 400

        rows, _ = ctx.interface.client.get_rows_with_filters(
            "file_share_links",
            equalities={"id": link_id},
            page_limit=1, page_num=0,
        )
        if not rows:
            return flask.jsonify({"ok": False, "message": "Share link not found."}), 404

        ctx.interface.client.update_rows_with_filters(
            "file_share_links",
            {"is_enabled": bool(data["is_enabled"])},
            equalities={"id": link_id},
        )
        return flask.jsonify({"ok": True, "message": "Share link updated."})

    # ------------------------------------------------------------------
    # Admin: delete any share link
    # ------------------------------------------------------------------

    @api.route("/api/admin/share/<link_id>", methods=["DELETE"])
    def api_admin_share_delete(link_id: str):
        user, err = require_admin(ctx)
        if err:
            return err

        rows, _ = ctx.interface.client.get_rows_with_filters(
            "file_share_links",
            equalities={"id": link_id},
            page_limit=1, page_num=0,
        )
        if not rows:
            return flask.jsonify({"ok": False, "message": "Share link not found."}), 404

        ctx.interface.client.delete_rows_with_filters("file_share_links", equalities={"id": link_id})
        return flask.jsonify({"ok": True, "message": "Share link deleted."})


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _delete_file_record(ctx: ApiContext, row: dict) -> None:
    """Remove disk file, delete DB record, decrement used_bytes."""
    user_id = str(row["user_id"])
    file_id = str(row["id"])
    size_bytes = int(row.get("size_bytes") or 0)
    path = _file_path(user_id, row["stored_name"])

    # Remove from disk first — if this fails we bail out.
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        logger.exception("Failed to remove file from disk: %s", path)

    # Delete DB record.
    ctx.interface.client.delete_rows_with_filters(
        "user_files",
        equalities={"id": file_id},
    )

    # Update used_bytes (best-effort).
    if size_bytes > 0:
        try:
            quota_rows, _ = ctx.interface.client.get_rows_with_filters(
                "user_storage_quotas",
                equalities={"user_id": user_id},
                page_limit=1,
                page_num=0,
            )
            if quota_rows:
                new_used = max(0, int(quota_rows[0]["used_bytes"] or 0) - size_bytes)
                ctx.interface.client.update_rows_with_filters(
                    "user_storage_quotas",
                    {"used_bytes": new_used},
                    equalities={"user_id": user_id},
                )
        except Exception:
            logger.exception("Failed to decrement used_bytes for user %s", user_id)


def _admin_used_bytes(ctx: ApiContext, user_id: str) -> int:
    try:
        rows = ctx.interface.execute_query(
            "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM user_files WHERE user_id = %s;",
            (user_id,),
        )
        return int((rows[0]["total"] if rows else 0) or 0)
    except Exception:
        return 0


def _serialize_file(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "original_name": row["original_name"],
        "mime_type": row.get("mime_type"),
        "size_bytes": row["size_bytes"],
        "download_count": row.get("download_count", 0),
        "created_at": _iso(row.get("created_at")),
    }


def _serialize_folder(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "file_count": int(row.get("file_count") or 0),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _serialize_share_link(row: dict) -> dict:
    target_type = row.get("target_type", "file")
    if target_type == "folder":
        target_name = row.get("folder_name") or "Unnamed folder"
        target_size = None
    else:
        target_name = row.get("file_name") or "Unknown file"
        target_size = row.get("file_size")
    return {
        "id": str(row["id"]),
        "target_type": target_type,
        "target_name": target_name,
        "target_size": target_size,
        "is_enabled": bool(row.get("is_enabled")),
        "download_count": int(row.get("download_count") or 0),
        "created_at": _iso(row.get("created_at")),
        "last_accessed_at": _iso(row.get("last_accessed_at")),
    }


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)
