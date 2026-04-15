/**
 * files_admin.js — Admin file storage portal
 *
 * Renders quota requests and all-files tables by fetching
 * /api/admin/files/quota/list and /api/admin/files/list.
 */

(function () {
    "use strict";

    const quotasEl  = document.querySelector("[data-files-admin-quotas]");
    const filesEl   = document.querySelector("[data-files-admin-files]");
    const summaryEl = document.querySelector("[data-files-admin-summary]");
    const linksEl   = document.querySelector("[data-files-admin-links]");

    if (!quotasEl && !filesEl && !linksEl) return;

    // ----------------------------------------------------------------
    // Helpers
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

    function statusBadge(status) {
        const colors = {
            pending:  "background:#e5a000;color:#fff;",
            approved: "background:#2ecc71;color:#fff;",
            denied:   "background:#e74c3c;color:#fff;",
            enabled:  "background:#2ecc71;color:#fff;",
            disabled: "background:#95a5a6;color:#fff;",
        };
        const style = colors[status] || "background:var(--border);color:var(--default);";
        return `<span class="badge" style="font-size:0.72rem;padding:0.15rem 0.5rem;border-radius:999px;${style}">${escHtml(status)}</span>`;
    }

    function mkCell(content, extraClass) {
        return `<div class="files-table__cell${extraClass ? " " + extraClass : ""}">${content}</div>`;
    }

    function mkHead(content, extraClass) {
        return `<div class="files-table__cell files-table__cell--head${extraClass ? " " + extraClass : ""}">${content}</div>`;
    }

    const GB = 1024 * 1024 * 1024;

    // ----------------------------------------------------------------
    // Quota table
    // ----------------------------------------------------------------

    function buildQuotaTable(quotas) {
        if (!quotas || quotas.length === 0) {
            return `<p class="files-table__empty" style="padding:1rem;text-align:center;color:var(--form_text);font-style:italic;">No quota records found.</p>`;
        }

        const head = `
            ${mkHead("User")}
            ${mkHead("Email", "files-admin__cell--email")}
            ${mkHead("Requested")}
            ${mkHead("Used")}
            ${mkHead("Status")}
            ${mkHead("Date", "files-admin__cell--date")}
            ${mkHead("Actions")}`;

        const rows = quotas.map(q => {
            const name = escHtml(`${q.first_name || ""} ${q.last_name || ""}`.trim() || "—");
            const noteTooltip = q.request_note
                ? `<span title="${escHtml(q.request_note)}" style="cursor:help;text-decoration:underline dotted;">note ℹ</span>`
                : "";

            const approveForm = `
            <div class="files-admin__approve-row" style="flex-wrap:wrap;gap:0.3rem;">
                <input class="files-admin__quota-input" type="number" min="1" max="51200"
                       placeholder="GB" title="Quota in GB"
                       data-approve-gb="${escHtml(q.user_id)}" style="width:5.5rem;">
                <button class="btn btn--primary" style="font-size:0.78rem;padding:0.25rem 0.5rem;"
                        data-approve="${escHtml(q.user_id)}">Approve</button>
                <button class="btn" style="font-size:0.78rem;padding:0.25rem 0.5rem;color:#e74c3c;"
                        data-deny="${escHtml(q.user_id)}">Deny</button>
            </div>`;

            return `
            <div class="files-table__row" data-quota-row="${escHtml(q.user_id)}">
                ${mkCell(name + (noteTooltip ? " " + noteTooltip : ""))}
                ${mkCell(escHtml(q.email || "—"), "files-admin__cell--email")}
                ${mkCell(fmtBytes(q.quota_bytes))}
                ${mkCell(fmtBytes(q.used_bytes))}
                ${mkCell(statusBadge(q.status))}
                ${mkCell(fmtDate(q.requested_at), "files-admin__cell--date")}
                ${mkCell(approveForm)}
            </div>`;
        }).join("\n");

        return `
        <div class="files-table-wrap" style="border-radius:0;border:none;">
            <div class="files-table files-admin-quota-table">
                ${head}${rows}
            </div>
        </div>`;
    }

    // ----------------------------------------------------------------
    // Files table
    // ----------------------------------------------------------------

    function buildFilesTable(files) {
        if (!files || files.length === 0) {
            return `<p class="files-table__empty" style="padding:1rem;text-align:center;color:var(--form_text);font-style:italic;">No files found.</p>`;
        }

        const head = `
            ${mkHead("User")}
            ${mkHead("Filename")}
            ${mkHead("Size", "files-admin__cell--size")}
            ${mkHead("Downloads", "files-admin__cell--dl")}
            ${mkHead("Uploaded", "files-admin__cell--date")}
            ${mkHead("Actions", "files-admin__cell--actions")}
            ${mkHead("", "files-admin__cell--menu")}`;

        const rows = files.map(f => {
            const name = escHtml(`${f.first_name || ""} ${f.last_name || ""}`.trim() || "—");
            return `
            <div class="files-table__row" data-file-row="${escHtml(f.id)}">
                ${mkCell(name)}
                ${mkCell(escHtml(f.original_name))}
                ${mkCell(fmtBytes(f.size_bytes), "files-admin__cell--size")}
                ${mkCell(String(f.download_count || 0), "files-admin__cell--dl")}
                ${mkCell(fmtDate(f.created_at), "files-admin__cell--date")}
                ${mkCell(`
                    <a class="icon-btn" href="/api/files/download/${escHtml(f.id)}" download title="Download">
                        <img class="icon-btn__img" src="/static/img/download.png" alt="Download">
                    </a>
                    <button class="icon-btn icon-btn--delete" title="Delete"
                            data-admin-delete="${escHtml(f.id)}">
                        <img class="icon-btn__img" src="/static/img/bin.png" alt="Delete">
                    </button>
                `, "files-admin__cell--actions")}
                ${mkCell(`
                    <button class="icon-btn icon-btn--menu" title="More options"
                            data-admin-menu="${escHtml(f.id)}"
                            data-admin-menu-download="/api/files/download/${escHtml(f.id)}">
                        <img class="icon-btn__img" src="/static/img/threedots.png" alt="More">
                    </button>
                `, "files-admin__cell--menu")}
            </div>`;
        }).join("\n");

        return `
        <div class="files-table-wrap" style="border-radius:0;border:none;">
            <div class="files-table files-admin-files-table">
                ${head}${rows}
            </div>
        </div>`;
    }

    // ----------------------------------------------------------------
    // Wire: quota actions
    // ----------------------------------------------------------------

    function wireQuotaActions() {
        quotasEl.querySelectorAll("[data-approve]").forEach(btn => {
            btn.addEventListener("click", async () => {
                const userId  = btn.dataset.approve;
                const gbInput = quotasEl.querySelector(`[data-approve-gb="${userId}"]`);
                const gb      = parseFloat(gbInput?.value || "0");
                if (!gb || gb <= 0 || gb > 51200) {
                    alert("Enter a valid quota in GB (1–51200).");
                    return;
                }
                const bytes = Math.round(gb * GB);
                await setQuota(userId, "approved", bytes, btn);
            });
        });

        quotasEl.querySelectorAll("[data-deny]").forEach(btn => {
            btn.addEventListener("click", async () => {
                if (!confirm("Deny this quota request?")) return;
                await setQuota(btn.dataset.deny, "denied", 0, btn);
            });
        });
    }

    async function setQuota(userId, status, quotaBytes, triggerBtn) {
        if (triggerBtn) triggerBtn.disabled = true;
        try {
            const body = { user_id: userId, status };
            if (status === "approved") body.quota_bytes = quotaBytes;

            const resp = await fetch("/api/admin/files/quota/set", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            if (data.ok) {
                init();
            } else {
                alert(data.message || "Action failed.");
                if (triggerBtn) triggerBtn.disabled = false;
            }
        } catch {
            alert("Network error.");
            if (triggerBtn) triggerBtn.disabled = false;
        }
    }

    // ----------------------------------------------------------------
    // Wire: admin file delete
    // ----------------------------------------------------------------

    function wireFileDeletes() {
        filesEl.querySelectorAll("[data-admin-delete]").forEach(btn => {
            btn.addEventListener("click", async () => {
                if (!confirm("Permanently delete this file?")) return;
                const fileId = btn.dataset.adminDelete;
                btn.disabled = true;
                try {
                    const resp = await fetch(`/api/admin/files/${fileId}`, { method: "DELETE" });
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
    // Context menu (mobile three-dots) — admin files
    // ----------------------------------------------------------------

    let _ctxMenu = null;
    let _ctxBackdrop = null;

    function _ensureCtxMenu() {
        if (!_ctxMenu) {
            _ctxMenu = document.createElement("div");
            _ctxMenu.className = "files-ctx-menu";
            _ctxMenu.setAttribute("role", "menu");
            document.body.appendChild(_ctxMenu);

            _ctxBackdrop = document.createElement("div");
            _ctxBackdrop.className = "files-ctx-menu__backdrop";
            _ctxBackdrop.addEventListener("click", _closeCtxMenu);
            document.body.appendChild(_ctxBackdrop);
        }
        return _ctxMenu;
    }

    function _closeCtxMenu() {
        if (_ctxMenu) _ctxMenu.classList.remove("files-ctx-menu--open");
        if (_ctxBackdrop) _ctxBackdrop.style.display = "none";
    }

    function _openAdminCtxMenu(btn, fileId, downloadHref) {
        const menu = _ensureCtxMenu();

        menu.innerHTML = `
            <a class="files-ctx-menu__item" role="menuitem"
               href="${escHtml(downloadHref)}" download>
                <img class="icon-btn__img" src="/static/img/download.png" alt="">Download
            </a>
            <button class="files-ctx-menu__item files-ctx-menu__item--delete icon-btn--ctx-delete" role="menuitem"
                    data-ctx-admin-delete="${escHtml(fileId)}">
                <img class="icon-btn__img" src="/static/img/bin.png" alt="">Delete
            </button>`;

        menu.querySelector("[data-ctx-admin-delete]")?.addEventListener("click", async () => {
            _closeCtxMenu();
            if (!confirm("Permanently delete this file?")) return;
            try {
                const resp = await fetch(`/api/admin/files/${fileId}`, { method: "DELETE" });
                const data = await resp.json();
                if (data.ok) { init(); } else { alert(data.message || "Delete failed."); }
            } catch { alert("Network error."); }
        });

        const rect = btn.getBoundingClientRect();
        const menuW = 180;
        let left = rect.right - menuW;
        if (left < 8) left = 8;
        let top = rect.bottom + 4;
        if (top + 120 > window.innerHeight) top = rect.top - 120 - 4;

        menu.style.left = left + "px";
        menu.style.top  = top  + "px";
        menu.classList.add("files-ctx-menu--open");
        _ctxBackdrop.style.display = "block";
    }

    function wireAdminMenuButtons() {
        filesEl.querySelectorAll("[data-admin-menu]").forEach(btn => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                _openAdminCtxMenu(btn, btn.dataset.adminMenu, btn.dataset.adminMenuDownload);
            });
        });
    }

    // ----------------------------------------------------------------
    // Share links table
    // ----------------------------------------------------------------

    function buildAdminLinksTable(links) {
        if (!links || links.length === 0) {
            return `<p class="files-table__empty" style="padding:1rem;text-align:center;color:var(--form_text);font-style:italic;">No share links found.</p>`;
        }

        const head = `
            ${mkHead("Owner")}
            ${mkHead("Target")}
            ${mkHead("Type")}
            ${mkHead("Downloads", "files-admin__cell--dl")}
            ${mkHead("Last Access", "files-admin__cell--date")}
            ${mkHead("Status")}
            ${mkHead("Actions")}`;

        const rows = links.map(lk => {
            const owner = escHtml(
                `${lk.owner_first_name || ""} ${lk.owner_last_name || ""}`.trim() || lk.owner_email || "—"
            );
            const shareUrl = `/share/${escHtml(lk.id)}`;
            const copyBtn = `<button class="btn" style="font-size:0.78rem;padding:0.2rem 0.45rem;" data-link-copy="${escHtml(lk.id)}" title="Copy link">Copy</button>`;
            const toggleBtn = `<button class="btn" style="font-size:0.78rem;padding:0.2rem 0.45rem;" data-link-toggle="${escHtml(lk.id)}" data-link-enabled="${lk.is_enabled ? "1" : "0"}">${lk.is_enabled ? "Disable" : "Enable"}</button>`;
            const deleteBtn = `<button class="btn" style="font-size:0.78rem;padding:0.2rem 0.45rem;color:#e74c3c;" data-link-delete="${escHtml(lk.id)}">Delete</button>`;

            return `
            <div class="files-table__row" data-link-row="${escHtml(lk.id)}">
                ${mkCell(owner)}
                ${mkCell(`<a href="${shareUrl}" target="_blank" rel="noopener" style="word-break:break-all;">${escHtml(lk.target_name)}</a>`)}
                ${mkCell(escHtml(lk.target_type))}
                ${mkCell(String(lk.download_count || 0), "files-admin__cell--dl")}
                ${mkCell(fmtDate(lk.last_accessed_at), "files-admin__cell--date")}
                ${mkCell(statusBadge(lk.is_enabled ? "enabled" : "disabled"))}
                ${mkCell(`<div style="display:flex;gap:0.3rem;flex-wrap:wrap;">${copyBtn}${toggleBtn}${deleteBtn}</div>`)}
            </div>`;
        }).join("\n");

        return `
        <div class="files-table-wrap" style="border-radius:0;border:none;">
            <div class="files-table files-admin-links-table">
                ${head}${rows}
            </div>
        </div>`;
    }

    // ----------------------------------------------------------------
    // Wire: share link actions
    // ----------------------------------------------------------------

    function wireAdminLinkActions() {
        linksEl.querySelectorAll("[data-link-copy]").forEach(btn => {
            btn.addEventListener("click", () => {
                const id = btn.dataset.linkCopy;
                const url = `${location.origin}/share/${id}`;
                navigator.clipboard.writeText(url).then(() => {
                    const orig = btn.textContent;
                    btn.textContent = "Copied!";
                    setTimeout(() => { btn.textContent = orig; }, 1500);
                }).catch(() => { alert(url); });
            });
        });

        linksEl.querySelectorAll("[data-link-toggle]").forEach(btn => {
            btn.addEventListener("click", async () => {
                const id = btn.dataset.linkToggle;
                const nowEnabled = btn.dataset.linkEnabled !== "1";
                btn.disabled = true;
                try {
                    const resp = await fetch(`/api/admin/share/${id}`, {
                        method: "PATCH",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ is_enabled: nowEnabled }),
                    });
                    const data = await resp.json();
                    if (data.ok) { init(); } else { alert(data.message || "Action failed."); btn.disabled = false; }
                } catch { alert("Network error."); btn.disabled = false; }
            });
        });

        linksEl.querySelectorAll("[data-link-delete]").forEach(btn => {
            btn.addEventListener("click", async () => {
                if (!confirm("Permanently delete this share link?")) return;
                const id = btn.dataset.linkDelete;
                btn.disabled = true;
                try {
                    const resp = await fetch(`/api/admin/share/${id}`, { method: "DELETE" });
                    const data = await resp.json();
                    if (data.ok) { init(); } else { alert(data.message || "Delete failed."); btn.disabled = false; }
                } catch { alert("Network error."); btn.disabled = false; }
            });
        });
    }

    // ----------------------------------------------------------------
    // Bootstrap
    // ----------------------------------------------------------------

    async function init() {
        if (quotasEl) quotasEl.innerHTML = `<div class="files-state-notice files-state-notice--loading">Loading…</div>`;
        if (filesEl)  filesEl.innerHTML  = `<div class="files-state-notice files-state-notice--loading">Loading…</div>`;
        if (linksEl)  linksEl.innerHTML  = `<div class="files-state-notice files-state-notice--loading">Loading…</div>`;

        try {
            const fetches = [];
            if (quotasEl) fetches.push(fetch("/api/admin/files/quota/list")); else fetches.push(Promise.resolve(null));
            if (filesEl)  fetches.push(fetch("/api/admin/files/list"));        else fetches.push(Promise.resolve(null));
            if (linksEl)  fetches.push(fetch("/api/admin/share/list"));        else fetches.push(Promise.resolve(null));

            const [quotaResp, filesResp, linksResp] = await Promise.all(fetches);

            if (quotasEl && quotaResp) {
                const quotaData = await quotaResp.json();
                quotasEl.innerHTML = buildQuotaTable(quotaData.ok ? quotaData.quotas : []);
                wireQuotaActions();
            }

            if (filesEl && filesResp) {
                const filesData   = await filesResp.json();
                const files       = filesData.ok ? (filesData.files || []) : [];
                const totalBytes  = filesData.ok ? (filesData.total_bytes || 0) : 0;
                const totalFiles  = filesData.ok ? (filesData.total_files || 0) : 0;

                filesEl.innerHTML = buildFilesTable(files);
                if (summaryEl) {
                    summaryEl.textContent = `${totalFiles} file${totalFiles !== 1 ? "s" : ""} · ${fmtBytes(totalBytes)} total`;
                }
                wireFileDeletes();
                wireAdminMenuButtons();
            }

            if (linksEl && linksResp) {
                const linksData = await linksResp.json();
                linksEl.innerHTML = buildAdminLinksTable(linksData.ok ? linksData.links : []);
                wireAdminLinkActions();
            }
        } catch (err) {
            const errHtml = `<div class="files-state-notice files-state-notice--error"><p>Failed to load data. Please refresh.</p></div>`;
            if (quotasEl) quotasEl.innerHTML = errHtml;
            if (filesEl)  filesEl.innerHTML  = errHtml;
            if (linksEl)  linksEl.innerHTML  = errHtml;
        }
    }

    document.addEventListener("DOMContentLoaded", init);
})();
