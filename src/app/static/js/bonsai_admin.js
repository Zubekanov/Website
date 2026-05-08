/**
 * bonsai_admin.js — Admin view for bonsai snapshots
 *
 * Fetches /api/admin/bonsai/images, renders a grid of thumbnails with
 * date labels and delete buttons.
 */

(function () {
    "use strict";

    const gridEl   = document.querySelector("[data-bonsai-admin-grid]");
    const summaryEl = document.querySelector("[data-bonsai-admin-summary]");
    if (!gridEl) return;

    // ----------------------------------------------------------------
    // Helpers
    // ----------------------------------------------------------------

    function fmtDate(iso) {
        if (!iso) return "—";
        try {
            return new Date(iso).toLocaleString(undefined, {
                year: "numeric", month: "short", day: "numeric",
                hour: "2-digit", minute: "2-digit",
            });
        } catch { return iso; }
    }

    function fmtBytes(n) {
        if (n === null || n === undefined) return "—";
        const units = ["B", "KB", "MB", "GB"];
        let v = Number(n);
        for (let i = 0; i < units.length - 1; i++) {
            if (v < 1024) return v.toFixed(1) + " " + units[i];
            v /= 1024;
        }
        return v.toFixed(1) + " GB";
    }

    function escHtml(s) {
        return String(s ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    // ----------------------------------------------------------------
    // Render
    // ----------------------------------------------------------------

    function buildGrid(images) {
        if (!images || images.length === 0) {
            return `<p class="files-state-notice" style="padding:1.5rem;text-align:center;color:var(--form_text);font-style:italic;">No snapshots found.</p>`;
        }

        const cards = images.map(img => `
            <div class="bonsai-admin__card" data-bonsai-card="${escHtml(img.id)}">
                <a href="/api/bonsai/images/${escHtml(img.id)}" target="_blank" rel="noopener">
                    <img class="bonsai-admin__thumb"
                         src="/api/bonsai/images/${escHtml(img.id)}"
                         alt="Snapshot ${escHtml(img.id)}"
                         loading="lazy">
                </a>
                <div class="bonsai-admin__meta">
                    <span class="bonsai-admin__date">${escHtml(fmtDate(img.captured_at))}</span>
                    <span class="bonsai-admin__size">${escHtml(fmtBytes(img.size_bytes))}</span>
                </div>
                <button class="btn bonsai-admin__delete-btn" data-bonsai-delete="${escHtml(img.id)}" title="Delete snapshot">
                    Delete
                </button>
            </div>`).join("\n");

        return `<div class="bonsai-admin__grid">${cards}</div>`;
    }

    // ----------------------------------------------------------------
    // Delete
    // ----------------------------------------------------------------

    function wireDeletes() {
        gridEl.querySelectorAll("[data-bonsai-delete]").forEach(btn => {
            btn.addEventListener("click", async () => {
                if (!confirm("Permanently delete this snapshot?")) return;
                const id = btn.dataset.bonsaiDelete;
                btn.disabled = true;
                try {
                    const resp = await fetch(`/api/admin/bonsai/images/${id}`, { method: "DELETE" });
                    const data = await resp.json();
                    if (data.ok) {
                        init();
                    } else {
                        alert(data.message || "Delete failed.");
                        btn.disabled = false;
                    }
                } catch {
                    alert("Network error.");
                    btn.disabled = false;
                }
            });
        });
    }

    // ----------------------------------------------------------------
    // Init
    // ----------------------------------------------------------------

    async function init() {
        gridEl.innerHTML = `<div class="files-state-notice files-state-notice--loading">Loading…</div>`;
        if (summaryEl) summaryEl.textContent = "";

        let data;
        try {
            const resp = await fetch("/api/admin/bonsai/images?limit=1000");
            data = await resp.json();
        } catch {
            gridEl.innerHTML = `<p class="files-state-notice files-state-notice--error">Failed to load snapshots.</p>`;
            return;
        }

        if (!data?.ok) {
            gridEl.innerHTML = `<p class="files-state-notice files-state-notice--error">${escHtml(data?.message || "Unknown error.")}</p>`;
            return;
        }

        if (summaryEl) {
            summaryEl.textContent = `${data.images.length} snapshot${data.images.length !== 1 ? "s" : ""}`;
        }

        gridEl.innerHTML = buildGrid(data.images);
        wireDeletes();
    }

    document.addEventListener("DOMContentLoaded", init);
})();
