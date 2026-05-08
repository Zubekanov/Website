/**
 * bonsai.js — Bonsai timelapse viewer
 *
 * Fetches /api/bonsai/images?daily=true, renders the most recent image,
 * and provides a video-player-style scrubber bar + play button.
 *
 * Playback speed: 2 fps (500 ms per frame).
 */

(function () {
    "use strict";

    const INTERVAL_MS = 500; // 2 fps

    const viewer = document.querySelector("[data-bonsai-viewer]");
    if (!viewer) return;

    let images      = [];  // chronological order (oldest → newest)
    let currentIdx  = 0;
    let playing     = false;
    let playTimer   = null;
    let scrubDrag   = false;

    // ----------------------------------------------------------------
    // Bootstrap
    // ----------------------------------------------------------------

    async function init() {
        let data;
        try {
            const resp = await fetch("/api/bonsai/images?daily=true&limit=1000");
            data = await resp.json();
        } catch { return; }

        if (!data?.ok || !data.images?.length) return;

        // API returns newest-first; reverse for chronological playback.
        images = [...data.images].reverse();
        currentIdx = images.length - 1;

        renderViewer();
        showFrame(currentIdx, false);
    }

    // ----------------------------------------------------------------
    // DOM build
    // ----------------------------------------------------------------

    function renderViewer() {
        viewer.innerHTML = `
            <img class="bonsai-viewer__img" id="bonsai-img" alt="Bonsai snapshot">
            <div class="bonsai-player" id="bonsai-player">
                <button class="bonsai-player__btn" id="bonsai-prev" title="Previous" aria-label="Previous frame">&#8249;</button>
                <button class="bonsai-player__btn bonsai-player__btn--play" id="bonsai-play" title="Play" aria-label="Play">&#9654;</button>
                <button class="bonsai-player__btn" id="bonsai-next" title="Next" aria-label="Next frame">&#8250;</button>
                <div class="bonsai-player__scrubber" id="bonsai-scrubber" role="slider"
                     aria-label="Timeline" aria-valuemin="0" aria-valuemax="${images.length - 1}">
                    <div class="bonsai-player__track">
                        <div class="bonsai-player__fill" id="bonsai-fill"></div>
                        <div class="bonsai-player__thumb" id="bonsai-thumb"></div>
                    </div>
                </div>
                <span class="bonsai-player__date" id="bonsai-date"></span>
                <span class="bonsai-player__count" id="bonsai-count"></span>
            </div>`;

        document.getElementById("bonsai-prev").addEventListener("click", () => { pause(); seek(currentIdx - 1); });
        document.getElementById("bonsai-next").addEventListener("click", () => { pause(); seek(currentIdx + 1); });
        document.getElementById("bonsai-play").addEventListener("click", togglePlay);

        const scrubber = document.getElementById("bonsai-scrubber");
        scrubber.addEventListener("mousedown",  onScrubStart);
        scrubber.addEventListener("touchstart", onScrubStart, { passive: true });
        window.addEventListener("mousemove",  onScrubMove);
        window.addEventListener("touchmove",  onScrubMove, { passive: true });
        window.addEventListener("mouseup",    onScrubEnd);
        window.addEventListener("touchend",   onScrubEnd);

        // Keyboard: left/right arrows when scrubber is focused.
        scrubber.setAttribute("tabindex", "0");
        scrubber.addEventListener("keydown", (e) => {
            if (e.key === "ArrowLeft")  { pause(); seek(currentIdx - 1); e.preventDefault(); }
            if (e.key === "ArrowRight") { pause(); seek(currentIdx + 1); e.preventDefault(); }
        });
    }

    // ----------------------------------------------------------------
    // Playback
    // ----------------------------------------------------------------

    function seek(idx) {
        showFrame(Math.max(0, Math.min(images.length - 1, idx)), true);
    }

    function showFrame(idx, preload) {
        currentIdx = idx;
        const frame = images[idx];

        const img   = document.getElementById("bonsai-img");
        const fill  = document.getElementById("bonsai-fill");
        const thumb = document.getElementById("bonsai-thumb");
        const date  = document.getElementById("bonsai-date");
        const count = document.getElementById("bonsai-count");

        if (img) img.src = `/api/bonsai/images/${frame.id}`;

        const pct = images.length > 1 ? (idx / (images.length - 1)) * 100 : 100;
        if (fill)  fill.style.width = pct + "%";
        if (thumb) thumb.style.left = pct + "%";

        if (date && frame.captured_at) {
            date.textContent = new Date(frame.captured_at).toLocaleDateString(undefined, {
                year: "numeric", month: "short", day: "numeric",
            });
        }
        if (count) count.textContent = `${idx + 1} / ${images.length}`;

        document.getElementById("bonsai-scrubber")?.setAttribute("aria-valuenow", idx);

        // Preload next frame while the current one is displaying.
        if (preload && idx + 1 < images.length) {
            const pre = new Image();
            pre.src = `/api/bonsai/images/${images[idx + 1].id}`;
        }
    }

    function togglePlay() {
        if (playing) { pause(); } else { play(); }
    }

    function play() {
        playing = true;
        updatePlayBtn();
        // Restart from beginning if we're at the last frame.
        if (currentIdx >= images.length - 1) showFrame(0, true);
        playTimer = setInterval(() => {
            if (currentIdx >= images.length - 1) { pause(); return; }
            showFrame(currentIdx + 1, true);
        }, INTERVAL_MS);
    }

    function pause() {
        playing = false;
        clearInterval(playTimer);
        playTimer = null;
        updatePlayBtn();
    }

    function updatePlayBtn() {
        const btn = document.getElementById("bonsai-play");
        if (!btn) return;
        btn.innerHTML    = playing
            ? `<img class="bonsai-player__pause-icon" src="/static/img/pause.png" alt="Pause">`
            : "&#9654;";
        btn.title        = playing ? "Pause" : "Play";
        btn.setAttribute("aria-label", playing ? "Pause" : "Play");
    }

    // ----------------------------------------------------------------
    // Scrubber drag
    // ----------------------------------------------------------------

    function clientX(e) {
        return e.touches ? e.touches[0].clientX : e.clientX;
    }

    function scrubberPctAt(e) {
        const track = document.querySelector(".bonsai-player__track");
        if (!track) return null;
        const rect = track.getBoundingClientRect();
        return Math.max(0, Math.min(1, (clientX(e) - rect.left) / rect.width));
    }

    function onScrubStart(e) {
        scrubDrag = true;
        pause();
        const pct = scrubberPctAt(e);
        if (pct !== null) seek(Math.round(pct * (images.length - 1)));
    }

    function onScrubMove(e) {
        if (!scrubDrag) return;
        const pct = scrubberPctAt(e);
        if (pct !== null) seek(Math.round(pct * (images.length - 1)));
    }

    function onScrubEnd() { scrubDrag = false; }

    // ----------------------------------------------------------------

    document.addEventListener("DOMContentLoaded", init);
})();
