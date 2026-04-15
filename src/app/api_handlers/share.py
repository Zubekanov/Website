from __future__ import annotations

import io
import logging
import os
import zipfile
from datetime import datetime, timezone

import flask

from app.api_context import ApiContext

logger = logging.getLogger(__name__)

# Maximum total uncompressed size for folder ZIP downloads.
_MAX_ZIP_BYTES = 500 * 1024 * 1024  # 500 MB

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


def _safe_read_path(upload_folder: str, user_id: str, stored_name: str) -> str:
    """Return the resolved absolute path; raise ValueError if it escapes the upload folder."""
    root = os.path.realpath(upload_folder)
    path = os.path.realpath(os.path.join(upload_folder, str(user_id), stored_name))
    if not path.startswith(root + os.sep):
        raise ValueError("Path traversal detected.")
    return path


def register(api: flask.Blueprint, ctx: ApiContext) -> None:

    # ------------------------------------------------------------------
    # Public: get share link metadata
    # ------------------------------------------------------------------

    @api.route("/api/share/<link_id>", methods=["GET"])
    def api_share_get(link_id: str):
        link, err = _resolve_link(ctx, link_id)
        if err:
            return err

        result = {
            "ok": True,
            "id": str(link["id"]),
            "target_type": link["target_type"],
            "is_enabled": bool(link["is_enabled"]),
            "download_count": int(link.get("download_count") or 0),
            "created_at": _iso(link.get("created_at")),
        }

        if link["target_type"] == "file":
            file_row = _get_file_for_link(ctx, link)
            if file_row is None:
                return flask.jsonify({"ok": False, "message": "Shared content no longer exists."}), 404
            result.update({
                "name": file_row["original_name"],
                "size_bytes": file_row["size_bytes"],
                "mime_type": file_row.get("mime_type"),
            })
        else:
            folder_row, files = _get_folder_for_link(ctx, link)
            if folder_row is None:
                return flask.jsonify({"ok": False, "message": "Shared content no longer exists."}), 404
            total_size = sum(int(f.get("size_bytes") or 0) for f in files)
            result.update({
                "name": folder_row["name"],
                "size_bytes": total_size,
                "files": [
                    {
                        "id": str(f["id"]),
                        "original_name": f["original_name"],
                        "size_bytes": f["size_bytes"],
                        "mime_type": f.get("mime_type"),
                    }
                    for f in files
                ],
            })

        return flask.jsonify(result)

    # ------------------------------------------------------------------
    # Public: download (file → stream, folder → zip)
    # ------------------------------------------------------------------

    @api.route("/api/share/<link_id>/download", methods=["GET"])
    def api_share_download(link_id: str):
        link, err = _resolve_link(ctx, link_id, require_enabled=True)
        if err:
            return err

        _record_access(ctx, link_id)

        upload_folder = flask.current_app.config["UPLOAD_FOLDER"]

        if link["target_type"] == "file":
            file_row = _get_file_for_link(ctx, link)
            if file_row is None:
                return flask.jsonify({"ok": False, "message": "File no longer exists."}), 404

            try:
                path = _safe_read_path(upload_folder, str(file_row["user_id"]), file_row["stored_name"])
            except ValueError:
                logger.error("Path traversal detected on share link %s", link_id)
                return flask.jsonify({"ok": False, "message": "File not found on disk."}), 404

            if not os.path.isfile(path):
                return flask.jsonify({"ok": False, "message": "File not found on disk."}), 404

            return flask.send_file(
                path,
                as_attachment=True,
                download_name=file_row["original_name"],
                mimetype=_safe_mime_type(file_row.get("mime_type")),
            )

        else:
            folder_row, files = _get_folder_for_link(ctx, link)
            if folder_row is None:
                return flask.jsonify({"ok": False, "message": "Folder no longer exists."}), 404
            if not files:
                return flask.jsonify({"ok": False, "message": "Folder is empty."}), 404

            zip_buf = _build_folder_zip(files, upload_folder)
            if zip_buf is None:
                return flask.jsonify({
                    "ok": False,
                    "message": "Folder is too large to download as a ZIP. Please download individual files.",
                }), 400
            safe_name = folder_row["name"].replace("/", "_").replace("\\", "_") or "folder"
            return flask.send_file(
                zip_buf,
                mimetype="application/zip",
                as_attachment=True,
                download_name=f"{safe_name}.zip",
            )

    # ------------------------------------------------------------------
    # Public: download a single file from a folder share link
    # ------------------------------------------------------------------

    @api.route("/api/share/<link_id>/files/<file_id>", methods=["GET"])
    def api_share_folder_file(link_id: str, file_id: str):
        link, err = _resolve_link(ctx, link_id, require_enabled=True)
        if err:
            return err
        if link["target_type"] != "folder":
            return flask.jsonify({"ok": False, "message": "Not a folder share link."}), 400

        _record_access(ctx, link_id)

        upload_folder = flask.current_app.config["UPLOAD_FOLDER"]
        folder_id = str(link["folder_id"])

        # Verify the file belongs to this folder.
        rows = ctx.interface.execute_query(
            """
            SELECT uf.id, uf.user_id, uf.original_name, uf.stored_name,
                   uf.mime_type, uf.size_bytes
            FROM file_folder_items fi
            JOIN user_files uf ON fi.file_id = uf.id
            WHERE fi.folder_id = %s AND uf.id = %s;
            """,
            (folder_id, file_id),
        )
        if not rows:
            return flask.jsonify({"ok": False, "message": "File not found in this shared folder."}), 404

        file_row = rows[0]
        try:
            path = _safe_read_path(upload_folder, str(file_row["user_id"]), file_row["stored_name"])
        except ValueError:
            logger.error("Path traversal detected on share link %s file %s", link_id, file_id)
            return flask.jsonify({"ok": False, "message": "File not found on disk."}), 404

        if not os.path.isfile(path):
            return flask.jsonify({"ok": False, "message": "File not found on disk."}), 404

        return flask.send_file(
            path,
            as_attachment=True,
            download_name=file_row["original_name"],
            mimetype=_safe_mime_type(file_row.get("mime_type")),
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _resolve_link(ctx: ApiContext, link_id: str, *, require_enabled: bool = False):
    """Return (link_row, None) or (None, error_response)."""
    try:
        rows, _ = ctx.interface.client.get_rows_with_filters(
            "file_share_links",
            equalities={"id": link_id},
            page_limit=1,
            page_num=0,
        )
    except Exception:
        return None, (flask.jsonify({"ok": False, "message": "Share link not found."}), 404)

    if not rows:
        return None, (flask.jsonify({"ok": False, "message": "Share link not found."}), 404)

    link = rows[0]
    if require_enabled and not link.get("is_enabled"):
        return None, (flask.jsonify({"ok": False, "message": "This share link has been disabled."}), 403)

    return link, None


def _get_file_for_link(ctx: ApiContext, link: dict):
    file_id = link.get("file_id")
    if not file_id:
        return None
    rows, _ = ctx.interface.client.get_rows_with_filters(
        "user_files",
        equalities={"id": str(file_id)},
        page_limit=1,
        page_num=0,
    )
    return rows[0] if rows else None


def _get_folder_for_link(ctx: ApiContext, link: dict):
    folder_id = link.get("folder_id")
    if not folder_id:
        return None, []

    folder_rows, _ = ctx.interface.client.get_rows_with_filters(
        "file_folders",
        equalities={"id": str(folder_id)},
        page_limit=1,
        page_num=0,
    )
    if not folder_rows:
        return None, []

    file_rows = ctx.interface.execute_query(
        """
        SELECT uf.id, uf.user_id, uf.original_name, uf.stored_name,
               uf.mime_type, uf.size_bytes
        FROM file_folder_items fi
        JOIN user_files uf ON fi.file_id = uf.id
        WHERE fi.folder_id = %s
        ORDER BY fi.added_at ASC;
        """,
        (str(folder_id),),
    )
    return folder_rows[0], (file_rows or [])


def _build_folder_zip(files: list[dict], upload_folder: str) -> io.BytesIO | None:
    """Build an in-memory ZIP of the given files.

    Returns None if the total uncompressed size exceeds _MAX_ZIP_BYTES.
    """
    total_bytes = sum(int(f.get("size_bytes") or 0) for f in files)
    if total_bytes > _MAX_ZIP_BYTES:
        return None

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen_names: dict[str, int] = {}
        for f in files:
            try:
                disk = _safe_read_path(upload_folder, str(f["user_id"]), f["stored_name"])
            except ValueError:
                logger.warning("Skipping file with suspicious path in zip build: user=%s stored=%s",
                               f["user_id"], f["stored_name"])
                continue
            if not os.path.isfile(disk):
                continue
            # Deduplicate filenames inside the zip.
            name = f["original_name"] or f["stored_name"]
            if name in seen_names:
                seen_names[name] += 1
                base, _, ext = name.rpartition(".")
                name = f"{base} ({seen_names[name]}).{ext}" if ext else f"{name} ({seen_names[name]})"
            else:
                seen_names[name] = 0
            try:
                zf.write(disk, name)
            except Exception:
                logger.warning("Failed to add %s to zip", disk)
    buf.seek(0)
    return buf


def _record_access(ctx: ApiContext, link_id: str) -> None:
    """Increment download_count and update last_accessed_at (best-effort)."""
    try:
        rows, _ = ctx.interface.client.get_rows_with_filters(
            "file_share_links",
            equalities={"id": link_id},
            page_limit=1,
            page_num=0,
        )
        if rows:
            ctx.interface.client.update_rows_with_filters(
                "file_share_links",
                {
                    "download_count": int(rows[0].get("download_count") or 0) + 1,
                    "last_accessed_at": datetime.now(timezone.utc),
                },
                equalities={"id": link_id},
            )
    except Exception:
        logger.warning("Failed to record access for share link %s", link_id)


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)
