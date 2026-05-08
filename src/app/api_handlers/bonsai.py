from __future__ import annotations

import hmac
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone

import flask

from app.api_context import ApiContext
from app.api_common import require_admin

logger = logging.getLogger(__name__)

_BONSAI_DIR    = "/HDD01/bonsai_images"
_TOKEN_PATH    = "/HDD01/bonsai_token"
_ALLOWED_MIME  = {"image/jpeg", "image/png", "image/webp"}
_UUID_RE       = __import__("re").compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", __import__("re").IGNORECASE)


# ------------------------------------------------------------------
# Startup init
# ------------------------------------------------------------------

def init_bonsai() -> None:
    """Ensure the image directory exists and the device token is set.

    Called from create_app() after verify_tables(). Safe to call in
    testing (dirs won't be on disk but errors are swallowed).
    """
    try:
        os.makedirs(_BONSAI_DIR, exist_ok=True)
    except OSError:
        logger.warning("Could not create bonsai image directory: %s", _BONSAI_DIR)

    token = _read_token()
    if token is None:
        token = secrets.token_urlsafe(32)
        try:
            with open(_TOKEN_PATH, "w") as fh:
                fh.write(token)
        except OSError:
            logger.warning("Could not write bonsai device token to %s", _TOKEN_PATH)

    logger.warning("Bonsai device token: %s", token)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _read_token() -> str | None:
    try:
        with open(_TOKEN_PATH) as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _check_token() -> bool:
    """Return True if the Bearer token in the request matches the stored token."""
    auth = flask.request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    incoming = auth[len("Bearer "):]
    stored = _read_token()
    if not stored:
        return False
    return hmac.compare_digest(incoming, stored)


# ------------------------------------------------------------------
# Route registration
# ------------------------------------------------------------------

def register(api: flask.Blueprint, ctx: ApiContext) -> None:

    # ------------------------------------------------------------------
    # Device: ingest an image
    # ------------------------------------------------------------------

    @api.route("/api/bonsai/ingest", methods=["POST"])
    def api_bonsai_ingest():
        if not _check_token():
            return flask.jsonify({"ok": False, "message": "Invalid or missing device token."}), 401

        content_type = (flask.request.content_type or "").split(";")[0].strip().lower()
        if content_type not in _ALLOWED_MIME:
            return flask.jsonify({
                "ok": False,
                "message": f"Unsupported image type '{content_type}'. Accepted: jpeg, png, webp.",
            }), 415

        # Parse optional X-Captured-At header; fall back to now.
        captured_at = datetime.now(timezone.utc)
        raw_ts = flask.request.headers.get("X-Captured-At", "").strip()
        if raw_ts:
            try:
                captured_at = datetime.fromisoformat(raw_ts)
                if captured_at.tzinfo is None:
                    captured_at = captured_at.replace(tzinfo=timezone.utc)
            except ValueError:
                return flask.jsonify({"ok": False, "message": "Invalid X-Captured-At value."}), 400

        stored_name = uuid.uuid4().hex
        dest = os.path.join(_BONSAI_DIR, stored_name)

        data = flask.request.stream.read()
        if not data:
            return flask.jsonify({"ok": False, "message": "Empty image body."}), 400

        try:
            with open(dest, "wb") as fh:
                fh.write(data)
        except OSError:
            logger.exception("Failed to write bonsai image to %s", dest)
            return flask.jsonify({"ok": False, "message": "Failed to save image."}), 500

        try:
            row = ctx.interface.client.insert_row("bonsai_images", {
                "stored_name": stored_name,
                "captured_at": captured_at,
                "size_bytes":  len(data),
                "mime_type":   content_type,
            })
        except Exception:
            logger.exception("Failed to insert bonsai_images row for %s", stored_name)
            try:
                os.remove(dest)
            except OSError:
                pass
            return flask.jsonify({"ok": False, "message": "Failed to record image."}), 500

        return flask.jsonify({"ok": True, "id": str(row["id"])})

    # ------------------------------------------------------------------
    # Public: list images
    # ------------------------------------------------------------------

    @api.route("/api/bonsai/images", methods=["GET"])
    def api_bonsai_images_list():
        try:
            limit = min(int(flask.request.args.get("limit", 365)), 1000)
        except (ValueError, TypeError):
            limit = 365

        daily  = flask.request.args.get("daily", "").lower() in ("1", "true", "yes")
        before = flask.request.args.get("before", "").strip()

        params: list = []
        where_clauses: list[str] = []

        if before:
            try:
                before_dt = datetime.fromisoformat(before)
                where_clauses.append("captured_at < %s")
                params.append(before_dt)
            except ValueError:
                return flask.jsonify({"ok": False, "message": "Invalid 'before' timestamp."}), 400

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        if daily:
            # Return the most recent image per UTC calendar day.
            query = f"""
                SELECT DISTINCT ON (DATE(captured_at AT TIME ZONE 'UTC'))
                    id, captured_at, size_bytes, mime_type
                FROM bonsai_images
                {where_sql}
                ORDER BY DATE(captured_at AT TIME ZONE 'UTC') DESC, captured_at DESC
                LIMIT %s;
            """
        else:
            query = f"""
                SELECT id, captured_at, size_bytes, mime_type
                FROM bonsai_images
                {where_sql}
                ORDER BY captured_at DESC
                LIMIT %s;
            """

        params.append(limit)
        rows = ctx.interface.execute_query(query, params) or []

        return flask.jsonify({
            "ok": True,
            "images": [
                {
                    "id":          str(r["id"]),
                    "captured_at": r["captured_at"].isoformat() if hasattr(r["captured_at"], "isoformat") else str(r["captured_at"]),
                    "size_bytes":  int(r["size_bytes"]),
                    "mime_type":   r.get("mime_type"),
                }
                for r in rows
            ],
        })

    # ------------------------------------------------------------------
    # Public: serve a single image
    # ------------------------------------------------------------------

    @api.route("/api/bonsai/images/<image_id>", methods=["GET"])
    def api_bonsai_image_serve(image_id: str):
        if not _UUID_RE.match(image_id):
            return flask.jsonify({"ok": False, "message": "Invalid image ID."}), 400

        rows = ctx.interface.execute_query(
            "SELECT stored_name, mime_type FROM bonsai_images WHERE id = %s LIMIT 1;",
            (image_id,),
        )
        if not rows:
            return flask.jsonify({"ok": False, "message": "Image not found."}), 404

        row  = rows[0]
        path = os.path.join(_BONSAI_DIR, row["stored_name"])
        if not os.path.isfile(path):
            return flask.jsonify({"ok": False, "message": "Image file not found on disk."}), 404

        resp = flask.send_file(
            path,
            mimetype=row.get("mime_type") or "image/jpeg",
            conditional=True,
        )
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    # ------------------------------------------------------------------
    # Admin: regenerate device token
    # ------------------------------------------------------------------

    @api.route("/api/admin/bonsai/token/regenerate", methods=["POST"])
    def api_admin_bonsai_token_regenerate():
        _, err = require_admin(ctx)
        if err:
            return err

        token = secrets.token_urlsafe(32)
        try:
            with open(_TOKEN_PATH, "w") as fh:
                fh.write(token)
        except OSError:
            logger.exception("Failed to write new bonsai device token")
            return flask.jsonify({"ok": False, "message": "Failed to write token."}), 500

        logger.warning("Bonsai device token regenerated: %s", token)
        return flask.jsonify({"ok": True, "token": token})

    # ------------------------------------------------------------------
    # Admin: list all images
    # ------------------------------------------------------------------

    @api.route("/api/admin/bonsai/images", methods=["GET"])
    def api_admin_bonsai_images_list():
        _, err = require_admin(ctx)
        if err:
            return err

        try:
            limit = min(int(flask.request.args.get("limit", 200)), 1000)
        except (ValueError, TypeError):
            limit = 200

        before = flask.request.args.get("before", "").strip()
        params: list = []
        where_clauses: list[str] = []

        if before:
            try:
                before_dt = datetime.fromisoformat(before)
                where_clauses.append("captured_at < %s")
                params.append(before_dt)
            except ValueError:
                return flask.jsonify({"ok": False, "message": "Invalid 'before' timestamp."}), 400

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        params.append(limit)

        rows = ctx.interface.execute_query(
            f"SELECT id, captured_at, size_bytes, mime_type FROM bonsai_images {where_sql} ORDER BY captured_at DESC LIMIT %s;",
            params,
        ) or []

        return flask.jsonify({
            "ok": True,
            "images": [
                {
                    "id":          str(r["id"]),
                    "captured_at": r["captured_at"].isoformat() if hasattr(r["captured_at"], "isoformat") else str(r["captured_at"]),
                    "size_bytes":  int(r["size_bytes"]),
                    "mime_type":   r.get("mime_type"),
                }
                for r in rows
            ],
        })

    # ------------------------------------------------------------------
    # Admin: delete an image
    # ------------------------------------------------------------------

    @api.route("/api/admin/bonsai/images/<image_id>", methods=["DELETE"])
    def api_admin_bonsai_image_delete(image_id: str):
        _, err = require_admin(ctx)
        if err:
            return err

        if not _UUID_RE.match(image_id):
            return flask.jsonify({"ok": False, "message": "Invalid image ID."}), 400

        rows = ctx.interface.execute_query(
            "SELECT stored_name FROM bonsai_images WHERE id = %s LIMIT 1;",
            (image_id,),
        )
        if not rows:
            return flask.jsonify({"ok": False, "message": "Image not found."}), 404

        stored_name = rows[0]["stored_name"]
        path = os.path.join(_BONSAI_DIR, stored_name)

        ctx.interface.client.delete_rows_with_filters(
            "bonsai_images", equalities={"id": image_id}
        )

        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            logger.warning("Could not remove bonsai image file: %s", path)

        return flask.jsonify({"ok": True})
