/**
 * files.js — Member file storage portal
 *
 * Renders quota status, upload zone, and file table by fetching
 * /api/files/quota and /api/files/list on page load.
 */

(function () {
    "use strict";

    const portal    = document.querySelector("[data-files-portal]");
    const content   = document.querySelector("[data-files-content]");

    if (!portal || !content) return;

    // ----------------------------------------------------------------
    // Sort state
    // ----------------------------------------------------------------

    let _files   = [];
    let _sortKey = null;   // 'name' | 'size' | 'date' | 'dl'
    let _sortDir = 1;      // 1 = asc, -1 = desc

    function _sortedFiles() {
        if (!_sortKey) return _files;
        return [..._files].sort((a, b) => {
            let va, vb;
            switch (_sortKey) {
                case "name": va = (a.original_name || "").toLowerCase(); vb = (b.original_name || "").toLowerCase(); break;
                case "size": va = Number(a.size_bytes || 0);             vb = Number(b.size_bytes || 0);             break;
                case "date": va = a.created_at || "";                    vb = b.created_at || "";                    break;
                case "dl":   va = Number(a.download_count || 0);         vb = Number(b.download_count || 0);         break;
                default:     return 0;
            }
            if (va < vb) return -_sortDir;
            if (va > vb) return  _sortDir;
            return 0;
        });
    }

    function _applySort(key) {
        if (_sortKey === key) { _sortDir = -_sortDir; }
        else { _sortKey = key; _sortDir = 1; }
        _renderFileSection();
    }

    // ----------------------------------------------------------------
    // Upload queue state  (module-level so it survives re-renders)
    // ----------------------------------------------------------------

    let _uploadQueue  = [];   // files waiting to be sent
    let _uploadActive = false;
    let _uploadTotal  = 0;    // total files in the current batch (for display)
    let _uploadDone   = 0;    // files successfully completed in this batch
    let _uploadAbort  = null; // callable that cancels the active upload

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

    function fmtDuration(ms) {
        const s = Math.round(ms / 1000);
        if (s < 60) return `${s}s`;
        const m = Math.floor(s / 60), r = s % 60;
        return r > 0 ? `${m}m ${r}s` : `${m}m`;
    }

    function fmtDate(iso) {
        if (!iso) return "—";
        try {
            return new Date(iso).toLocaleDateString(undefined, {
                year: "numeric", month: "short", day: "numeric"
            });
        } catch { return iso; }
    }

    function escapeHtml(s) {
        return String(s ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function notice(type, html) {
        return `<div class="files-state-notice files-state-notice--${escapeHtml(type)}">${html}</div>`;
    }

    // ----------------------------------------------------------------
    // Quota bar
    // ----------------------------------------------------------------

    function buildQuotaBar(quota) {
        const used  = Number(quota.used_bytes  || 0);
        const total = Number(quota.quota_bytes || 0);
        const pct   = total > 0 ? Math.min(100, (used / total) * 100) : 0;
        const fillClass = pct >= 90 ? "files-quota__bar-fill--full"
                        : pct >= 70 ? "files-quota__bar-fill--warn"
                        : "";
        const adminTag = quota.is_admin
            ? `<span style="font-size:0.75rem;color:var(--dark_blue);font-weight:600;">Admin (unlimited)</span>`
            : "";
        return `
        <div class="files-quota">
            <div class="files-quota__label">
                <span>Storage used: <strong>${fmtBytes(used)}</strong> of <strong>${fmtBytes(total)}</strong></span>
                ${adminTag}
                <span>${pct.toFixed(1)}%</span>
            </div>
            <div class="files-quota__bar">
                <div class="files-quota__bar-fill ${fillClass}" style="width:${pct}%"></div>
            </div>
        </div>`;
    }

    // ----------------------------------------------------------------
    // Sort pills (mobile only)
    // ----------------------------------------------------------------

    function buildSortPills() {
        const defs = [
            { label: "Size",      key: "size" },
            { label: "Uploaded",  key: "date" },
            { label: "Downloads", key: "dl"   },
        ];
        const pills = defs.map(d => {
            const active = _sortKey === d.key;
            const arrow  = active ? `<span class="files-sort-pill__arrow">${_sortDir === 1 ? "↑" : "↓"}</span>` : "";
            return `<button class="files-sort-pill${active ? " files-sort-pill--active" : ""}"
                            type="button" data-sort-key="${d.key}">${escapeHtml(d.label)} ${arrow}</button>`;
        }).join("");
        return `<div class="files-sort-pills">${pills}</div>`;
    }

    // ----------------------------------------------------------------
    // File table
    // ----------------------------------------------------------------

    function _sortHead(label, key, extraClass) {
        const active = _sortKey === key;
        const arrow  = active ? `<span class="files-table__sort-arrow">${_sortDir === 1 ? "↑" : "↓"}</span>` : "";
        const cls = `files-table__cell files-table__cell--head files-table__cell--sortable${active ? " files-table__cell--head-sorted" : ""}${extraClass ? " " + extraClass : ""}`;
        return `<div class="${cls}" role="columnheader" tabindex="0" data-sort-key="${key}">${escapeHtml(label)}${arrow}</div>`;
    }

    function buildFileTable(files) {
        let rows = "";
        if (!files || files.length === 0) {
            rows = `<div class="files-table__empty">No files uploaded yet.</div>`;
        } else {
            rows = files.map(f => {
            const large   = Number(f.size_bytes || 0) > 1073741824; // 1 GB
            const dlHref  = large
                ? `/api/files/download/${escapeHtml(f.id)}/zip`
                : `/api/files/download/${escapeHtml(f.id)}`;
            const dlAttr  = large
                ? `download="${escapeHtml(f.original_name)}.zip"`
                : "download";
            const dlTitle = large ? "Download as ZIP" : "Download";
            return `
            <div class="files-table__row" data-file-id="${escapeHtml(f.id)}">
                <div class="files-table__cell files-table__cell--name" data-editable-name tabindex="0">${escapeHtml(f.original_name)}</div>
                <div class="files-table__cell files-table__cell--size">${fmtBytes(f.size_bytes)}</div>
                <div class="files-table__cell files-table__cell--date">${fmtDate(f.created_at)}</div>
                <div class="files-table__cell files-table__cell--dl-count">${Number(f.download_count || 0)}</div>
                <div class="files-table__cell files-table__actions files-table__cell--actions">
                    <button class="icon-btn" title="Share"
                            data-share-file="${escapeHtml(f.id)}"
                            data-share-file-name="${escapeHtml(f.original_name)}">
                        <img class="icon-btn__img" src="/static/img/share.png" alt="Share">
                    </button>
                    <a class="icon-btn" href="${dlHref}" ${dlAttr} title="${dlTitle}">
                        <img class="icon-btn__img" src="/static/img/download.png" alt="Download">
                    </a>
                    <button class="icon-btn icon-btn--delete" title="Delete"
                            data-delete-file="${escapeHtml(f.id)}">
                        <img class="icon-btn__img" src="/static/img/bin.png" alt="Delete">
                    </button>
                </div>
                <div class="files-table__cell files-table__cell--menu">
                    <button class="icon-btn icon-btn--menu" title="More options"
                            data-menu-file="${escapeHtml(f.id)}"
                            data-menu-download="${dlHref}"
                            data-menu-file-name="${escapeHtml(f.original_name)}">
                        <img class="icon-btn__img" src="/static/img/threedots.png" alt="More">
                    </button>
                </div>
            </div>`;
        }).join("\n");
        }

        return `
        <div class="files-table-wrap">
            <div class="files-table">
                ${_sortHead("Name",      "name")}
                ${_sortHead("Size",      "size", "files-table__cell--size")}
                ${_sortHead("Uploaded",  "date", "files-table__cell--date")}
                ${_sortHead("Downloads", "dl",   "files-table__cell--dl-count")}
                <div class="files-table__cell files-table__cell--head files-table__cell--actions">Actions</div>
                <div class="files-table__cell files-table__cell--head files-table__cell--menu"></div>
                ${rows}
            </div>
        </div>`;
    }

    // ----------------------------------------------------------------
    // Upload zone
    // ----------------------------------------------------------------

    function buildUploadZone() {
        return `
        <div class="files-upload-zone" id="files-drop-zone" role="button" tabindex="0"
             aria-label="Drop files here or click to upload">
            <p class="files-upload-zone__label">Drop files here, or</p>
            <button class="btn btn--primary files-upload-zone__btn" type="button" id="files-pick-btn">Choose files</button>
            <input type="file" id="files-input" style="display:none" aria-hidden="true" multiple>
            <div class="files-upload-progress" id="files-progress">
                <div class="files-upload-progress__fill" id="files-progress-fill" style="width:0%"></div>
            </div>
            <p id="files-upload-status" style="font-size:0.8rem;color:var(--form_text);margin:0.25rem 0 0;min-height:1em;"></p>
            <button class="btn files-upload-zone__cancel-btn" type="button" id="files-cancel-btn"
                    style="display:none;margin-top:0.5rem;">Cancel</button>
        </div>`;
    }

    // ----------------------------------------------------------------
    // Quota request form
    // ----------------------------------------------------------------

    function buildRequestForm(existingStatus, adminNote) {
        const GB = 1024 * 1024 * 1024;
        const isReRequest = existingStatus === "denied";

        let header = `<p class="files-request-form__title">Request Storage Quota</p>`;
        if (isReRequest) {
            const note = adminNote ? `<p style="margin:0;font-size:0.82rem;"><strong>Admin note:</strong> ${escapeHtml(adminNote)}</p>` : "";
            header += `
            <div class="files-state-notice files-state-notice--warn">
                <p>Your previous request was denied.</p>
                ${note}
                <p>You may submit a new request below.</p>
            </div>`;
        } else {
            header += `<p class="files-request-form__desc">Choose how much space you need and explain why. An admin will review your request.</p>`;
        }

        return `
        <div class="files-request-form" id="files-request-form">
            ${header}
            <div class="form-group">
                <label for="files-quota-size">Requested size</label>
                <select id="files-quota-size">
                    <option value="${1 * GB}">1 GB</option>
                    <option value="${5 * GB}" selected>5 GB</option>
                    <option value="${10 * GB}">10 GB</option>
                    <option value="${20 * GB}">20 GB</option>
                    <option value="${50 * GB}">50 GB</option>
                </select>
            </div>
            <div class="form-group">
                <label for="files-request-note">Reason for request</label>
                <textarea id="files-request-note" placeholder="Describe what you'll use the storage for…" rows="4"></textarea>
            </div>
            <button class="btn btn--primary" type="button" id="files-submit-request">Submit request</button>
            <p class="files-request-form__result" id="files-request-result"></p>
        </div>`;
    }

    // ----------------------------------------------------------------
    // Render based on quota state
    // ----------------------------------------------------------------

    function renderState(quota, files) {
        const status = quota ? quota.status : "none";

        if (status === "none") {
            content.innerHTML = buildRequestForm(null, null);
            wireRequestForm();
            return;
        }

        if (status === "pending") {
            content.innerHTML = notice("info", "<p><strong>Your quota request is awaiting admin approval.</strong></p><p>You will be able to upload files once your request is approved.</p>");
            return;
        }

        if (status === "denied") {
            content.innerHTML = buildRequestForm("denied", quota.admin_note || null);
            wireRequestForm();
            return;
        }

        // Approved (or admin).
        _files = files;
        let html = buildQuotaBar(quota);
        html += buildUploadZone();
        html += `<div data-files-section></div>`;
        html += `<div data-folders-section></div>`;
        html += `<div data-share-section></div>`;
        content.innerHTML = html;
        wireUploadZone(quota);
        _renderFileSection();
        _renderFoldersSection();
        _renderShareSection();
    }

    // ----------------------------------------------------------------
    // Wire: quota request form
    // ----------------------------------------------------------------

    function wireRequestForm() {
        const btn    = document.getElementById("files-submit-request");
        const result = document.getElementById("files-request-result");
        if (!btn) return;

        btn.addEventListener("click", async () => {
            const sizeEl = document.getElementById("files-quota-size");
            const noteEl = document.getElementById("files-request-note");
            const note   = (noteEl?.value || "").trim();
            const bytes  = parseInt(sizeEl?.value || "0", 10);

            if (!note) {
                if (result) { result.style.color = "#e74c3c"; result.textContent = "Please provide a reason."; }
                return;
            }

            btn.disabled = true;
            if (result) { result.style.color = "var(--form_text)"; result.textContent = "Submitting…"; }

            try {
                const resp = await fetch("/api/files/quota/request", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ quota_bytes: bytes, note }),
                });
                const data = await resp.json();
                if (data.ok) {
                    content.innerHTML = notice("info", "<p><strong>Request submitted.</strong> An admin will review it shortly.</p>");
                } else {
                    if (result) { result.style.color = "#e74c3c"; result.textContent = data.message || "Request failed."; }
                    btn.disabled = false;
                }
            } catch {
                if (result) { result.style.color = "#e74c3c"; result.textContent = "Network error. Please try again."; }
                btn.disabled = false;
            }
        });
    }

    // ----------------------------------------------------------------
    // Wire: upload zone (drag-drop + click)
    // ----------------------------------------------------------------

    function wireUploadZone(quota) {
        const zone      = document.getElementById("files-drop-zone");
        const pickBtn   = document.getElementById("files-pick-btn");
        const cancelBtn = document.getElementById("files-cancel-btn");
        const input     = document.getElementById("files-input");
        const progress  = document.getElementById("files-progress");
        const fill      = document.getElementById("files-progress-fill");
        const statusEl  = document.getElementById("files-upload-status");

        if (!zone || !input) return;

        // Cancel button — calls whatever abort function the active upload registered.
        cancelBtn?.addEventListener("click", (e) => {
            e.stopPropagation();
            if (_uploadAbort) _uploadAbort();
        });

        // Warn before navigating away while an upload is in progress.
        window.addEventListener("beforeunload", (e) => {
            if (_uploadActive) { e.preventDefault(); }
        }, { once: false });

        pickBtn?.addEventListener("click", (e) => { e.stopPropagation(); input.click(); });
        zone.addEventListener("click", () => { if (!_uploadActive) input.click(); });
        zone.addEventListener("keydown", e => { if ((e.key === "Enter" || e.key === " ") && !_uploadActive) input.click(); });

        zone.addEventListener("dragover",  (e) => { e.preventDefault(); zone.classList.add("files-upload-zone--drag-over"); });
        zone.addEventListener("dragleave", ()  => { zone.classList.remove("files-upload-zone--drag-over"); });
        zone.addEventListener("drop", (e) => {
            e.preventDefault();
            zone.classList.remove("files-upload-zone--drag-over");
            if (e.dataTransfer?.files?.length) enqueue(e.dataTransfer.files, quota);
        });

        input.addEventListener("change", () => {
            if (input.files?.length) enqueue(input.files, quota);
            input.value = "";  // reset so the same file can be re-selected later
        });

        // ------------------------------------------------------------------
        // Queue management
        // ------------------------------------------------------------------

        function enqueue(fileList, q) {
            const incoming = Array.from(fileList);
            if (incoming.length === 0) return;

            if (!_uploadActive && _uploadQueue.length === 0) {
                // Fresh batch — reset counters.
                _uploadTotal = incoming.length;
                _uploadDone  = 0;
            } else {
                _uploadTotal += incoming.length;
            }
            _uploadQueue.push(...incoming);
            if (!_uploadActive) startNext(q);
        }

        function startNext(q) {
            if (_uploadQueue.length === 0) {
                _uploadActive = false;
                _uploadAbort  = null;
                if (cancelBtn) cancelBtn.style.display = "none";
                if (fill)     fill.style.width = "100%";
                if (statusEl) {
                    statusEl.style.color = "#2ecc71";
                    statusEl.textContent = _uploadTotal === 1
                        ? "Upload complete."
                        : `${_uploadTotal} files uploaded.`;
                }
                init();
                return;
            }
            _uploadActive = true;
            if (cancelBtn) cancelBtn.style.display = "";
            doUpload(_uploadQueue.shift(), q);
        }

        async function doUpload(file, q) {
            const available = q ? (Number(q.quota_bytes || 0) - Number(q.used_bytes || 0)) : Infinity;
            if (!q?.is_admin && file.size > available) {
                _uploadQueue  = [];
                _uploadActive = false;
                if (statusEl) { statusEl.style.color = "#e74c3c"; statusEl.textContent = `"${file.name}" is too large. Available: ${fmtBytes(available)}.`; }
                if (pickBtn)  pickBtn.disabled = false;
                return;
            }

            const pos        = _uploadDone + 1;
            const queueLabel = _uploadTotal > 1 ? ` (${pos} of ${_uploadTotal})` : "";

            if (progress) progress.classList.add("files-upload-progress--visible");
            if (fill)     fill.style.width = "0%";
            if (statusEl) { statusEl.style.color = "var(--form_text)"; statusEl.textContent = `Uploading${queueLabel}…`; }
            if (pickBtn)  pickBtn.disabled = true;

            function onSuccess() {
                _uploadAbort  = null;
                _uploadDone++;
                if (q) q.used_bytes = Number(q.used_bytes || 0) + file.size;
                startNext(q);
            }
            function onError(msg) {
                _uploadAbort  = null;
                _uploadQueue  = [];
                _uploadActive = false;
                if (cancelBtn) cancelBtn.style.display = "none";
                if (statusEl) { statusEl.style.color = "#e74c3c"; statusEl.textContent = msg || "Upload failed."; }
                if (pickBtn)  pickBtn.disabled = false;
                if (fill)     fill.style.width = "0%";
            }
            function onCancelled() {
                _uploadAbort  = null;
                _uploadQueue  = [];
                _uploadActive = false;
                if (cancelBtn) cancelBtn.style.display = "none";
                if (statusEl) { statusEl.style.color = "var(--form_text)"; statusEl.textContent = "Upload cancelled."; }
                if (pickBtn)  pickBtn.disabled = false;
                if (fill)     fill.style.width = "0%";
            }

            const CHUNK_SIZE = 16 * 1024 * 1024; // 16 MB per chunk

            if (file.size < CHUNK_SIZE) {
                // Small file: single request with cautious animated guess bar.
                let fakePct = 0;
                const fakeTimer = setInterval(() => {
                    fakePct = Math.min(fakePct + (88 - fakePct) * 0.07, 88);
                    if (fill) fill.style.width = fakePct.toFixed(1) + "%";
                }, 120);

                await new Promise((resolve) => {
                    const xhr = new XMLHttpRequest();
                    xhr.open("POST", "/api/files/upload");
                    xhr.setRequestHeader("X-Filename", encodeURIComponent(file.name));
                    xhr.setRequestHeader("Content-Type", "application/octet-stream");
                    xhr.addEventListener("load", () => {
                        clearInterval(fakeTimer);
                        let data = {};
                        try { data = JSON.parse(xhr.responseText); } catch {}
                        if (data.ok) { onSuccess(); } else { onError(data.message); }
                        resolve();
                    });
                    xhr.addEventListener("abort", () => {
                        clearInterval(fakeTimer);
                        onCancelled();
                        resolve();
                    });
                    xhr.addEventListener("error", () => {
                        clearInterval(fakeTimer);
                        onError("Network error.");
                        resolve();
                    });
                    _uploadAbort = () => xhr.abort();
                    xhr.send(file);
                });
            } else {
                // Large file: split into 16 MB chunks, progress advances per chunk.
                const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
                const uploadId = crypto.randomUUID();
                const controller = new AbortController();
                _uploadAbort = () => controller.abort();
                try {
                    const chunkTimes = []; // ms per completed chunk, oldest first
                    for (let i = 0; i < totalChunks; i++) {
                        const start = i * CHUNK_SIZE;
                        const end   = Math.min(start + CHUNK_SIZE, file.size);
                        const blob  = file.slice(start, end);

                        const t0 = Date.now();
                        const resp = await fetch("/api/files/upload/chunk", {
                            method: "POST",
                            signal: controller.signal,
                            headers: {
                                "Content-Type":   "application/octet-stream",
                                "X-Upload-Id":    uploadId,
                                "X-Chunk-Index":  String(i),
                                "X-Total-Chunks": String(totalChunks),
                                "X-Filename":     encodeURIComponent(file.name),
                                "X-Total-Size":   String(file.size),
                            },
                            body: blob,
                        });
                        chunkTimes.push(Date.now() - t0);
                        let data = {};
                        try { data = await resp.json(); } catch {}
                        if (!data.ok) { onError(data.message); return; }

                        const pct = ((i + 1) / totalChunks * 100).toFixed(1);
                        if (fill) fill.style.width = pct + "%";

                        const remaining = totalChunks - (i + 1);
                        let etaStr = "";
                        if (remaining > 0) {
                            // Average over the last `remaining` chunks (or all if fewer recorded).
                            const window = chunkTimes.slice(-remaining);
                            const avgMs  = window.reduce((a, b) => a + b, 0) / window.length;
                            etaStr = " — ~" + fmtDuration(remaining * avgMs);
                        }
                        const chunkLabel = totalChunks > 100 ? ` (chunk ${i + 1}/${totalChunks})` : "";
                        if (statusEl) statusEl.textContent = `Uploading${queueLabel} ${Math.round(pct)}%${chunkLabel}${etaStr}`;
                    }

                    const resp = await fetch("/api/files/upload/complete", {
                        method: "POST",
                        signal: controller.signal,
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ upload_id: uploadId, filename: file.name, total_size: file.size }),
                    });
                    let data = {};
                    try { data = await resp.json(); } catch {}
                    if (data.ok) { onSuccess(); } else { onError(data.message); }
                } catch (err) {
                    if (err?.name === "AbortError") { onCancelled(); } else { onError("Network error."); }
                }
            }
        }
    }

    // ----------------------------------------------------------------
    // Wire: delete buttons
    // ----------------------------------------------------------------

    function wireDeleteButtons() {
        content.querySelectorAll("[data-delete-file]").forEach(btn => {
            btn.addEventListener("click", async () => {
                const fileId = btn.dataset.deleteFile;
                if (!confirm("Delete this file? This cannot be undone.")) return;

                btn.disabled = true;
                try {
                    const resp = await fetch(`/api/files/${fileId}`, { method: "DELETE" });
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
    // Wire: inline rename (click name cell to edit in place)
    // ----------------------------------------------------------------

    function _wireInlineRename() {
        content.querySelectorAll("[data-editable-name]").forEach(cell => {
            cell.addEventListener("click", () => {
                if (cell.querySelector("input")) return;

                const row     = cell.closest("[data-file-id]");
                const fileId  = row?.dataset.fileId;
                if (!fileId) return;

                const current = cell.textContent;
                const input   = document.createElement("input");
                input.className = "files-table__name-input";
                input.type  = "text";
                input.value = current;

                cell.textContent = "";
                cell.appendChild(input);
                input.focus();
                input.select();

                let committed = false;

                async function commit() {
                    if (committed) return;
                    committed = true;
                    const newName = input.value.trim();
                    if (!newName || newName === current) { cell.textContent = current; return; }
                    input.disabled = true;
                    try {
                        const resp = await fetch(`/api/files/${fileId}`, {
                            method: "PATCH",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ name: newName }),
                        });
                        const data = await resp.json();
                        if (data.ok) {
                            const f = _files.find(x => x.id === fileId);
                            if (f) f.original_name = data.name;
                            cell.textContent = data.name;
                            row.querySelector("[data-menu-file-name]")?.setAttribute("data-menu-file-name", data.name);
                            row.querySelector("[data-share-file-name]")?.setAttribute("data-share-file-name", data.name);
                        } else {
                            cell.textContent = current;
                        }
                    } catch {
                        cell.textContent = current;
                    }
                }

                input.addEventListener("blur", commit);
                input.addEventListener("keydown", e => {
                    if (e.key === "Enter")  { e.preventDefault(); input.blur(); }
                    if (e.key === "Escape") { committed = true; cell.textContent = current; }
                });
            });
        });
    }

    // ----------------------------------------------------------------
    // Context menu (mobile three-dots)
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

    function _openCtxMenu(btn, fileId, downloadHref, fileName) {
        const menu = _ensureCtxMenu();

        function _showMain() {
            menu.innerHTML = `
                <button class="files-ctx-menu__item" role="menuitem"
                        data-ctx-share="${escapeHtml(fileId)}"
                        data-ctx-share-name="${escapeHtml(fileName || "")}">
                    <img class="icon-btn__img" src="/static/img/share.png" alt="">Share
                </button>
                <a class="files-ctx-menu__item" role="menuitem"
                   href="${escapeHtml(downloadHref)}" download>
                    <img class="icon-btn__img" src="/static/img/download.png" alt="">Download
                </a>
                <button class="files-ctx-menu__item" role="menuitem" data-ctx-rename>
                    Rename
                </button>
                <button class="files-ctx-menu__item" role="menuitem" data-ctx-add-to-folder>
                    Add to folder ▶
                </button>
                <button class="files-ctx-menu__item files-ctx-menu__item--delete icon-btn--ctx-delete" role="menuitem"
                        data-ctx-delete="${escapeHtml(fileId)}">
                    <img class="icon-btn__img" src="/static/img/bin.png" alt="">Delete
                </button>`;

            menu.querySelector("[data-ctx-share]")?.addEventListener("click", () => {
                _closeCtxMenu();
                _createShareLink("file", fileId, fileName || "file", null);
            });

            menu.querySelector("[data-ctx-rename]")?.addEventListener("click", () => {
                _closeCtxMenu();
                content.querySelector(`[data-file-id="${fileId}"] [data-editable-name]`)?.click();
            });

            menu.querySelector("[data-ctx-delete]")?.addEventListener("click", async () => {
                _closeCtxMenu();
                if (!confirm("Delete this file? This cannot be undone.")) return;
                try {
                    const resp = await fetch(`/api/files/${fileId}`, { method: "DELETE" });
                    const data = await resp.json();
                    if (data.ok) { init(); } else { alert(data.message || "Delete failed."); }
                } catch { alert("Network error."); }
            });

            menu.querySelector("[data-ctx-add-to-folder]")?.addEventListener("click", _showFolderPicker);
        }

        async function _showFolderPicker() {
            menu.innerHTML = `<div class="files-ctx-menu__item files-ctx-menu__item--label">
                    <button class="files-ctx-menu__back" data-ctx-back>← Back</button>
                    Add to folder
                </div>
                <div data-ctx-folder-list style="padding:0.3rem 0;">
                    <span style="padding:0.4rem 0.9rem;display:block;font-size:0.8rem;color:var(--form_text);">Loading…</span>
                </div>`;
            menu.querySelector("[data-ctx-back]")?.addEventListener("click", _showMain);

            try {
                const resp = await fetch("/api/files/folders");
                const data = await resp.json();
                const folders = data.ok ? (data.folders || []) : [];
                const list = menu.querySelector("[data-ctx-folder-list]");
                if (folders.length === 0) {
                    list.innerHTML = `<span style="padding:0.4rem 0.9rem;display:block;font-size:0.8rem;color:var(--form_text);font-style:italic;">No folders yet.</span>`;
                } else {
                    list.innerHTML = folders.map(f => `
                        <button class="files-ctx-menu__item" role="menuitem" data-ctx-folder-id="${escapeHtml(f.id)}">
                            ${escapeHtml(f.name)}
                            <span style="margin-left:auto;font-size:0.75rem;color:var(--form_text);">${f.file_count}</span>
                        </button>`).join("");
                    list.querySelectorAll("[data-ctx-folder-id]").forEach(fb => {
                        fb.addEventListener("click", async () => {
                            fb.disabled = true;
                            try {
                                const r = await fetch(`/api/files/folders/${fb.dataset.ctxFolderId}/items`, {
                                    method: "POST",
                                    headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify({ file_id: fileId }),
                                });
                                const d = await r.json();
                                _closeCtxMenu();
                                if (d.ok) { _renderFoldersSection(); }
                                else { alert(d.message || "Could not add to folder."); }
                            } catch { _closeCtxMenu(); alert("Network error."); }
                        });
                    });
                }
            } catch {
                menu.querySelector("[data-ctx-folder-list]").innerHTML =
                    `<span style="padding:0.4rem 0.9rem;display:block;font-size:0.8rem;color:#e74c3c;">Failed to load.</span>`;
            }
        }

        _showMain();

        // Position below the button, keep within viewport
        const rect = btn.getBoundingClientRect();
        const menuW = 180;
        let left = rect.right - menuW;
        if (left < 8) left = 8;
        let top = rect.bottom + 4;
        if (top + 200 > window.innerHeight) top = rect.top - 200 - 4;

        menu.style.left = left + "px";
        menu.style.top  = top  + "px";
        menu.classList.add("files-ctx-menu--open");

        _ctxBackdrop.style.display = "block";
    }

    function wireMenuButtons() {
        content.querySelectorAll("[data-menu-file]").forEach(btn => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                const fileId   = btn.dataset.menuFile;
                const href     = btn.dataset.menuDownload;
                const fileName = btn.dataset.menuFileName || "";
                _openCtxMenu(btn, fileId, href, fileName);
            });
        });
    }

    function _wireShareFileButtons() {
        content.querySelectorAll("[data-share-file]").forEach(btn => {
            btn.addEventListener("click", () => {
                _createShareLink("file", btn.dataset.shareFile, btn.dataset.shareFileName || "file", btn);
            });
        });
    }

    // ----------------------------------------------------------------
    // File section render + sort wiring
    // ----------------------------------------------------------------

    function _renderFileSection() {
        const section = content.querySelector("[data-files-section]");
        if (!section) return;
        section.innerHTML = buildSortPills() + buildFileTable(_sortedFiles());
        wireDeleteButtons();
        _wireInlineRename();
        wireMenuButtons();
        _wireTableSort();
        _wireShareFileButtons();
    }

    function _wireTableSort() {
        content.querySelectorAll("[data-sort-key]").forEach(el => {
            el.addEventListener("click", () => _applySort(el.dataset.sortKey));
            el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") _applySort(el.dataset.sortKey); });
        });
    }

    // ----------------------------------------------------------------
    // Folders panel
    // ----------------------------------------------------------------

    async function _renderFoldersSection() {
        const section = content.querySelector("[data-folders-section]");
        if (!section) return;
        section.innerHTML = `<div class="files-state-notice files-state-notice--loading" style="font-size:0.85rem;">Loading folders…</div>`;
        try {
            const resp = await fetch("/api/files/folders");
            const data = await resp.json();
            const folders = data.ok ? (data.folders || []) : [];
            section.innerHTML = _buildFoldersPanel(folders);
            _wireFoldersPanel(section);
        } catch {
            section.innerHTML = "";
        }
    }

    function _buildFoldersPanel(folders) {
        const rows = folders.length === 0
            ? `<p class="files-panel__empty">No folders yet.</p>`
            : folders.map(f => `
            <div class="files-folder-row" data-folder-id="${escapeHtml(f.id)}">
                <button class="files-folder-row__expand btn btn--xs" type="button"
                        data-folder-expand="${escapeHtml(f.id)}" title="Expand folder">▶</button>
                <span class="files-folder-row__name">${escapeHtml(f.name)}</span>
                <span class="files-folder-row__count">${f.file_count} file${f.file_count !== 1 ? "s" : ""}</span>
                <button class="btn btn--xs" title="Add file to folder"
                        data-folder-add="${escapeHtml(f.id)}">+ Add</button>
                <button class="icon-btn" title="Share folder"
                        data-share-folder="${escapeHtml(f.id)}"
                        data-share-folder-name="${escapeHtml(f.name)}">
                    <img class="icon-btn__img" src="/static/img/share.png" alt="Share">
                </button>
                <button class="icon-btn icon-btn--delete" title="Delete folder"
                        data-delete-folder="${escapeHtml(f.id)}">
                    <img class="icon-btn__img" src="/static/img/bin.png" alt="Delete">
                </button>
            </div>
            <div class="files-folder-contents" data-folder-contents="${escapeHtml(f.id)}" hidden></div>
            <div class="files-folder-add-picker" data-folder-add-picker="${escapeHtml(f.id)}" hidden></div>`
            ).join("");

        return `
        <div class="files-panel">
            <div class="files-panel__header">
                <span class="files-panel__title">Folders</span>
            </div>
            <div class="files-folder-list">${rows}</div>
        </div>`;
    }

    function _wireFoldersPanel(section) {
        // Delete folder
        section.querySelectorAll("[data-delete-folder]").forEach(btn => {
            btn.addEventListener("click", async () => {
                if (!confirm("Delete this folder? Files inside will not be deleted.")) return;
                btn.disabled = true;
                try {
                    const resp = await fetch(`/api/files/folders/${btn.dataset.deleteFolder}`, { method: "DELETE" });
                    const data = await resp.json();
                    if (data.ok) { _renderFoldersSection(); }
                    else { alert(data.message || "Failed to delete folder."); btn.disabled = false; }
                } catch { alert("Network error."); btn.disabled = false; }
            });
        });

        // Expand folder
        section.querySelectorAll("[data-folder-expand]").forEach(btn => {
            btn.addEventListener("click", async () => {
                const folderId = btn.dataset.folderExpand;
                const contentsEl = section.querySelector(`[data-folder-contents="${folderId}"]`);
                if (!contentsEl) return;
                if (!contentsEl.hidden) {
                    contentsEl.hidden = true;
                    btn.textContent = "▶";
                    return;
                }
                btn.textContent = "▼";
                contentsEl.hidden = false;
                contentsEl.innerHTML = `<span style="font-size:0.8rem;color:var(--form_text);padding:0.4rem 0.75rem;display:block;">Loading…</span>`;
                try {
                    const resp = await fetch(`/api/files/folders/${folderId}`);
                    const data = await resp.json();
                    const files = data.ok ? (data.files || []) : [];
                    if (files.length === 0) {
                        contentsEl.innerHTML = `<span style="font-size:0.8rem;color:var(--form_text);padding:0.4rem 0.75rem;display:block;font-style:italic;">Empty folder.</span>`;
                    } else {
                        contentsEl.innerHTML = `<ul class="files-folder-contents__list">${files.map(f => `
                            <li class="files-folder-contents__item">
                                <span class="files-folder-contents__name">${escapeHtml(f.original_name)}</span>
                                <span class="files-folder-contents__size" style="color:var(--form_text);font-size:0.8rem;">${fmtBytes(f.size_bytes)}</span>
                                <button class="icon-btn icon-btn--delete" title="Remove from folder"
                                        data-remove-from-folder="${escapeHtml(folderId)}"
                                        data-remove-file="${escapeHtml(f.id)}">
                                    <img class="icon-btn__img" src="/static/img/bin.png" alt="Remove">
                                </button>
                            </li>`).join("")}</ul>`;
                        contentsEl.querySelectorAll("[data-remove-from-folder]").forEach(rb => {
                            rb.addEventListener("click", async () => {
                                rb.disabled = true;
                                await fetch(`/api/files/folders/${rb.dataset.removeFromFolder}/items/${rb.dataset.removeFile}`, { method: "DELETE" });
                                rb.closest("li")?.remove();
                            });
                        });
                    }
                } catch {
                    contentsEl.innerHTML = `<span style="font-size:0.8rem;color:#e74c3c;padding:0.4rem 0.75rem;display:block;">Failed to load.</span>`;
                }
            });
        });

        // Share folder → create link
        section.querySelectorAll("[data-share-folder]").forEach(btn => {
            btn.addEventListener("click", () => _createShareLink("folder", btn.dataset.shareFolder, btn.dataset.shareFolderName, btn));
        });

        // Add file to folder
        section.querySelectorAll("[data-folder-add]").forEach(btn => {
            btn.addEventListener("click", async () => {
                const folderId = btn.dataset.folderAdd;
                const picker = section.querySelector(`[data-folder-add-picker="${folderId}"]`);
                if (!picker) return;

                // Toggle off if already open
                if (!picker.hidden) {
                    picker.hidden = true;
                    return;
                }

                picker.hidden = false;
                picker.innerHTML = `<span class="files-folder-add-picker__loading">Loading files…</span>`;

                try {
                    const resp = await fetch("/api/files/list");
                    const data = await resp.json();
                    const files = (data.ok ? (data.files || []) : [])
                        .filter(f => f.id !== undefined);

                    if (files.length === 0) {
                        picker.innerHTML = `<span class="files-folder-add-picker__empty">No files to add.</span>`;
                        return;
                    }

                    picker.innerHTML = `
                        <input type="text" class="files-folder-add-picker__search"
                               placeholder="Search files…" autocomplete="off">
                        <ul class="files-folder-add-picker__list">${files.map(f => `
                            <li>
                                <button class="files-folder-add-picker__item" type="button"
                                        data-picker-file-id="${escapeHtml(f.id)}"
                                        data-picker-file-name="${escapeHtml(f.original_name)}">
                                    ${escapeHtml(f.original_name)}
                                    <span class="files-folder-add-picker__size">${fmtBytes(f.size_bytes)}</span>
                                </button>
                            </li>`).join("")}
                        </ul>`;

                    // Search filter
                    const searchInput = picker.querySelector(".files-folder-add-picker__search");
                    const listItems = picker.querySelectorAll(".files-folder-add-picker__list li");
                    searchInput?.addEventListener("input", () => {
                        const q = searchInput.value.toLowerCase();
                        listItems.forEach(li => {
                            const name = li.querySelector("[data-picker-file-name]")?.dataset.pickerFileName || "";
                            li.hidden = !name.toLowerCase().includes(q);
                        });
                    });

                    picker.querySelectorAll("[data-picker-file-id]").forEach(fb => {
                        fb.addEventListener("click", async () => {
                            fb.disabled = true;
                            try {
                                const r = await fetch(`/api/files/folders/${folderId}/items`, {
                                    method: "POST",
                                    headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify({ file_id: fb.dataset.pickerFileId }),
                                });
                                const d = await r.json();
                                picker.hidden = true;
                                if (d.ok) { _renderFoldersSection(); }
                                else { alert(d.message || "Could not add file."); fb.disabled = false; }
                            } catch { alert("Network error."); fb.disabled = false; }
                        });
                    });
                } catch {
                    picker.innerHTML = `<span class="files-folder-add-picker__empty" style="color:#e74c3c;">Failed to load files.</span>`;
                }
            });
        });
    }

    // ----------------------------------------------------------------
    // Share links panel
    // ----------------------------------------------------------------

    async function _renderShareSection() {
        const section = content.querySelector("[data-share-section]");
        if (!section) return;
        section.innerHTML = `<div class="files-state-notice files-state-notice--loading" style="font-size:0.85rem;">Loading share links…</div>`;
        try {
            const resp = await fetch("/api/files/share");
            const data = await resp.json();
            const links = data.ok ? (data.links || []) : [];
            section.innerHTML = _buildSharePanel(links);
            _wireSharePanel(section);
        } catch {
            section.innerHTML = "";
        }
    }

    function _buildSharePanel(links) {
        let rows;
        if (links.length === 0) {
            rows = `<p class="files-panel__empty">No share links yet. Use the share button on a file or folder to create one.</p>`;
        } else {
            rows = `<div class="files-share-list">${links.map(lnk => {
                const url = `${location.origin}/share/${escapeHtml(lnk.id)}`;
                const badge = lnk.is_enabled
                    ? `<span class="files-share-badge files-share-badge--on">On</span>`
                    : `<span class="files-share-badge files-share-badge--off">Off</span>`;
                return `
                <div class="files-share-row" data-share-link-id="${escapeHtml(lnk.id)}">
                    <div class="files-share-row__info">
                        <span class="files-share-row__name">${escapeHtml(lnk.target_name)}</span>
                        <span class="files-share-row__type">${escapeHtml(lnk.target_type)}</span>
                        ${badge}
                        <span class="files-share-row__dl">${lnk.download_count} dl</span>
                    </div>
                    <div class="files-share-row__actions">
                        <button class="btn btn--xs files-share-row__copy" type="button"
                                data-copy-link="${escapeHtml(url)}" title="Copy link">Copy link</button>
                        <button class="icon-btn files-share-row__toggle" title="${lnk.is_enabled ? "Disable" : "Enable"} link"
                                data-toggle-link="${escapeHtml(lnk.id)}"
                                data-toggle-enabled="${lnk.is_enabled ? "true" : "false"}">
                            <img class="icon-btn__img" src="/static/img/${lnk.is_enabled ? "share" : "share"}.png" alt="Toggle">
                        </button>
                        <button class="icon-btn icon-btn--delete" title="Delete link"
                                data-delete-link="${escapeHtml(lnk.id)}">
                            <img class="icon-btn__img" src="/static/img/bin.png" alt="Delete">
                        </button>
                    </div>
                </div>`;
            }).join("")}</div>`;
        }
        return `
        <div class="files-panel">
            <div class="files-panel__header">
                <span class="files-panel__title">Share Links</span>
            </div>
            ${rows}
        </div>`;
    }

    function _wireSharePanel(section) {
        section.querySelectorAll("[data-copy-link]").forEach(btn => {
            btn.addEventListener("click", async () => {
                try {
                    await navigator.clipboard.writeText(btn.dataset.copyLink);
                    const orig = btn.textContent;
                    btn.textContent = "Copied!";
                    setTimeout(() => { btn.textContent = orig; }, 1500);
                } catch { alert(btn.dataset.copyLink); }
            });
        });

        section.querySelectorAll("[data-toggle-link]").forEach(btn => {
            btn.addEventListener("click", async () => {
                const linkId  = btn.dataset.toggleLink;
                const enabled = btn.dataset.toggleEnabled === "true";
                btn.disabled  = true;
                try {
                    const resp = await fetch(`/api/files/share/${linkId}`, {
                        method: "PATCH",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ is_enabled: !enabled }),
                    });
                    const data = await resp.json();
                    if (data.ok) { _renderShareSection(); }
                    else { alert(data.message || "Failed."); btn.disabled = false; }
                } catch { alert("Network error."); btn.disabled = false; }
            });
        });

        section.querySelectorAll("[data-delete-link]").forEach(btn => {
            btn.addEventListener("click", () => {
                _showConfirmModal({
                    title: "Delete share link",
                    body: "Anyone with the URL will no longer be able to access the shared content.",
                    confirmLabel: "Delete",
                    danger: true,
                    onConfirm: async () => {
                        btn.disabled = true;
                        try {
                            const resp = await fetch(`/api/files/share/${btn.dataset.deleteLink}`, { method: "DELETE" });
                            const data = await resp.json();
                            if (data.ok) { _renderShareSection(); }
                            else { alert(data.message || "Failed."); btn.disabled = false; }
                        } catch { alert("Network error."); btn.disabled = false; }
                    },
                });
            });
        });
    }

    // ----------------------------------------------------------------
    // Generic confirm modal
    // ----------------------------------------------------------------

    function _showConfirmModal({ title, body, confirmLabel = "Confirm", danger = false, onConfirm }) {
        document.querySelector("[data-confirm-modal]")?.remove();

        const modal = document.createElement("div");
        modal.setAttribute("data-confirm-modal", "");
        modal.className = "files-share-modal-backdrop";
        modal.innerHTML = `
            <div class="files-share-modal" role="dialog" aria-modal="true">
                <div class="files-share-modal__header">
                    <span class="files-share-modal__title">${escapeHtml(title)}</span>
                    <button class="files-share-modal__close" data-modal-close aria-label="Close">✕</button>
                </div>
                <div class="files-share-modal__body">
                    <p class="files-share-modal__hint" style="color:var(--default);font-size:0.9rem;">${escapeHtml(body)}</p>
                </div>
                <div class="files-share-modal__footer">
                    <button class="btn" data-modal-close>Cancel</button>
                    <button class="btn ${danger ? "btn--danger" : "btn--primary"}" data-modal-confirm>${escapeHtml(confirmLabel)}</button>
                </div>
            </div>`;

        function _close() {
            modal.classList.add("files-share-modal-backdrop--out");
            modal.addEventListener("animationend", () => modal.remove(), { once: true });
        }

        modal.querySelectorAll("[data-modal-close]").forEach(b => b.addEventListener("click", _close));
        modal.addEventListener("click", e => { if (e.target === modal) _close(); });
        modal.querySelector("[data-modal-confirm]").addEventListener("click", () => {
            _close();
            onConfirm();
        });

        document.body.appendChild(modal);
    }

    // ----------------------------------------------------------------
    // Share-link created modal
    // ----------------------------------------------------------------

    function _showShareModal(url, targetName, existing) {
        // Remove any existing modal
        document.querySelector("[data-share-modal]")?.remove();

        const modal = document.createElement("div");
        modal.setAttribute("data-share-modal", "");
        modal.className = "files-share-modal-backdrop";
        modal.innerHTML = `
            <div class="files-share-modal" role="dialog" aria-modal="true" aria-label="Share link created">
                <div class="files-share-modal__header">
                    <span class="files-share-modal__title">${existing ? "Share link" : "Share link created"}</span>
                    <button class="files-share-modal__close" data-modal-close aria-label="Close">✕</button>
                </div>
                <div class="files-share-modal__body">
                    <p class="files-share-modal__name">${escapeHtml(targetName)}</p>
                    <div class="files-share-modal__url-row">
                        <input class="files-share-modal__url-input" type="text" readonly
                               value="${escapeHtml(url)}" aria-label="Share URL">
                        <button class="btn btn--primary files-share-modal__copy-btn" data-modal-copy>Copy</button>
                    </div>
                    <p class="files-share-modal__hint">Anyone with this link can view and download the shared content.</p>
                </div>
                <div class="files-share-modal__footer">
                    <a class="btn" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Open link</a>
                    <button class="btn btn--primary" data-modal-close>Done</button>
                </div>
            </div>`;

        function _close() {
            modal.classList.add("files-share-modal-backdrop--out");
            modal.addEventListener("animationend", () => modal.remove(), { once: true });
        }

        modal.querySelectorAll("[data-modal-close]").forEach(b => b.addEventListener("click", _close));
        modal.addEventListener("click", e => { if (e.target === modal) _close(); });

        const copyBtn = modal.querySelector("[data-modal-copy]");
        const urlInput = modal.querySelector(".files-share-modal__url-input");
        copyBtn.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(url);
            } catch {
                urlInput.select();
                document.execCommand("copy");
            }
            copyBtn.textContent = "Copied!";
            setTimeout(() => { copyBtn.textContent = "Copy"; }, 2000);
        });

        document.body.appendChild(modal);
        // Auto-select the URL for easy manual copy
        setTimeout(() => urlInput.select(), 50);
    }

    // ----------------------------------------------------------------
    // Create share link (used by file share button + folder share button)
    // ----------------------------------------------------------------

    async function _createShareLink(targetType, targetId, targetName, triggerBtn) {
        if (triggerBtn) triggerBtn.disabled = true;
        try {
            const resp = await fetch("/api/files/share", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ target_type: targetType, target_id: targetId }),
            });
            const data = await resp.json();
            if (data.ok) {
                const url = `${location.origin}${data.url}`;
                _showShareModal(url, targetName, data.existing);
                _renderShareSection();
            } else {
                alert(data.message || "Failed to create share link.");
            }
        } catch {
            alert("Network error.");
        }
        if (triggerBtn) triggerBtn.disabled = false;
    }

    // ----------------------------------------------------------------
    // Bootstrap
    // ----------------------------------------------------------------

    async function init() {
        content.innerHTML = `<div class="files-state-notice files-state-notice--loading">Loading…</div>`;

        try {
            const [quotaResp, listResp] = await Promise.all([
                fetch("/api/files/quota"),
                fetch("/api/files/list"),
            ]);

            const quotaData = await quotaResp.json();
            const listData  = await listResp.json();

            const quota = quotaData.ok ? quotaData : null;
            const files = listData.ok ? (listData.files || []) : [];

            renderState(quota, files);
        } catch (err) {
            content.innerHTML = `<div class="files-state-notice files-state-notice--error"><p>Failed to load file storage data. Please refresh.</p></div>`;
        }
    }

    document.addEventListener("DOMContentLoaded", init);
})();
