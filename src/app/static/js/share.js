/**
 * share.js — Public share page
 *
 * Reads data-share-link-id from the root element, fetches
 * /api/share/<link_id>, then renders file or folder view.
 */

(function () {
    "use strict";

    const root = document.querySelector("[data-share-page]");
    if (!root) return;

    const linkId = root.dataset.shareLinkId;
    if (!linkId) return;

    // ----------------------------------------------------------------
    // Formatting helpers
    // ----------------------------------------------------------------

    function fmtBytes(n) {
        if (n === null || n === undefined) return "—";
        const units = ["B", "KB", "MB", "GB", "TB"];
        let v = Number(n);
        for (let i = 0; i < units.length - 1; i++) {
            if (v < 1024) return v.toFixed(1) + " " + units[i];
            v /= 1024;
        }
        return v.toFixed(1) + " TB";
    }

    function fmtDate(iso) {
        if (!iso) return "—";
        try {
            return new Date(iso).toLocaleDateString(undefined, {
                year: "numeric", month: "short", day: "numeric"
            });
        } catch { return iso; }
    }

    function escHtml(s) {
        return String(s ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    // ----------------------------------------------------------------
    // Render: not available
    // ----------------------------------------------------------------

    function renderNotAvailable(message) {
        root.innerHTML = `
        <div class="share-page__card share-page__card--notice">
            <div class="share-page__notice-icon">⚠</div>
            <p class="share-page__notice-text">${escHtml(message || "This shared link is not available.")}</p>
            <a class="btn" href="/">Go to homepage</a>
        </div>`;
    }

    // ----------------------------------------------------------------
    // Render: file view
    // ----------------------------------------------------------------

    const _1GB  = 1024 * 1024 * 1024;
    const _100MB = 100 * 1024 * 1024;

    function renderFile(data) {
        document.title = `${data.name} — Shared File`;
        const size = Number(data.size_bytes || 0);

        let actionsHtml;
        if (size > _1GB) {
            // Large file: mandatory ZIP only
            actionsHtml = `
                <a class="btn btn--primary share-page__download-btn"
                   href="/api/share/${escHtml(linkId)}/download/zip"
                   download="${escHtml(data.name)}.zip">
                    Download as ZIP
                </a>`;
        } else if (size > _100MB) {
            // Medium file: both options
            actionsHtml = `
                <a class="btn btn--primary share-page__download-btn"
                   href="/api/share/${escHtml(linkId)}/download"
                   download="${escHtml(data.name)}">
                    Download
                </a>
                <a class="btn share-page__download-btn"
                   href="/api/share/${escHtml(linkId)}/download/zip"
                   download="${escHtml(data.name)}.zip">
                    Download as ZIP
                </a>`;
        } else {
            actionsHtml = `
                <a class="btn btn--primary share-page__download-btn"
                   href="/api/share/${escHtml(linkId)}/download"
                   download="${escHtml(data.name)}">
                    Download
                </a>`;
        }

        root.innerHTML = `
        <div class="share-page__card">
            <div class="share-page__icon share-page__icon--file" aria-hidden="true">
                <img src="/static/img/download.png" alt="" class="share-page__icon-img">
            </div>
            <h1 class="share-page__name">${escHtml(data.name)}</h1>
            <dl class="share-page__meta">
                <div class="share-page__meta-row">
                    <dt>Size</dt>
                    <dd>${fmtBytes(data.size_bytes)}</dd>
                </div>
                <div class="share-page__meta-row">
                    <dt>Shared on</dt>
                    <dd>${fmtDate(data.created_at)}</dd>
                </div>
                <div class="share-page__meta-row">
                    <dt>Downloads</dt>
                    <dd>${Number(data.download_count || 0)}</dd>
                </div>
            </dl>
            <div class="share-page__actions">${actionsHtml}
            </div>
        </div>`;
    }

    // ----------------------------------------------------------------
    // Render: folder view
    // ----------------------------------------------------------------

    function renderFolder(data) {
        document.title = `${data.name} — Shared Folder`;
        const files = data.files || [];
        const fileRows = files.length === 0
            ? `<li class="share-page__file-row share-page__file-row--empty">This folder is empty.</li>`
            : files.map(f => `
            <li class="share-page__file-row">
                <span class="share-page__file-name">${escHtml(f.original_name)}</span>
                <span class="share-page__file-size">${fmtBytes(f.size_bytes)}</span>
                <a class="icon-btn share-page__file-dl"
                   href="/api/share/${escHtml(linkId)}/files/${escHtml(f.id)}"
                   download="${escHtml(f.original_name)}"
                   title="Download ${escHtml(f.original_name)}">
                    <img class="icon-btn__img" src="/static/img/download.png" alt="Download">
                </a>
            </li>`).join("");

        root.innerHTML = `
        <div class="share-page__card">
            <div class="share-page__icon share-page__icon--folder" aria-hidden="true">
                <img src="/static/img/share.png" alt="" class="share-page__icon-img">
            </div>
            <h1 class="share-page__name">${escHtml(data.name)}</h1>
            <dl class="share-page__meta">
                <div class="share-page__meta-row">
                    <dt>Files</dt>
                    <dd>${files.length}</dd>
                </div>
                <div class="share-page__meta-row">
                    <dt>Total size</dt>
                    <dd>${fmtBytes(data.size_bytes)}</dd>
                </div>
                <div class="share-page__meta-row">
                    <dt>Shared on</dt>
                    <dd>${fmtDate(data.created_at)}</dd>
                </div>
                <div class="share-page__meta-row">
                    <dt>Downloads</dt>
                    <dd>${Number(data.download_count || 0)}</dd>
                </div>
            </dl>
            <div class="share-page__actions">
                <a class="btn btn--primary share-page__download-btn"
                   href="/api/share/${escHtml(linkId)}/download">
                    Download all as ZIP
                </a>
            </div>
            <ul class="share-page__file-list">${fileRows}</ul>
        </div>`;
    }

    // ----------------------------------------------------------------
    // Bootstrap
    // ----------------------------------------------------------------

    async function init() {
        try {
            const resp = await fetch(`/api/share/${linkId}`);
            if (resp.status === 404 || resp.status === 403) {
                const data = await resp.json().catch(() => ({}));
                renderNotAvailable(data.message || "This shared link is not available.");
                return;
            }
            const data = await resp.json();
            if (!data.ok) {
                renderNotAvailable(data.message || "This shared link is not available.");
                return;
            }
            if (!data.is_enabled) {
                renderNotAvailable("This share link has been disabled by the owner.");
                return;
            }
            if (data.target_type === "folder") {
                renderFolder(data);
            } else {
                renderFile(data);
            }
        } catch {
            renderNotAvailable("Failed to load shared content. Please try again.");
        }
    }

    document.addEventListener("DOMContentLoaded", init);
})();
