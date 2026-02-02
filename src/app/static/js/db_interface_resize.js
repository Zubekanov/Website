(() => {
	"use strict";

	function initGrid(grid) {
		const colCount = Number(grid.dataset.colCount || "0");
		if (!colCount) return;

		const headerCells = Array.from(grid.querySelectorAll(".db-cell--head[data-col-index]"));
		if (headerCells.length === 0) return;

		const actionsWidth = Math.max(120, Number(grid.dataset.actionsWidth || "160"));
		const minW = 80;
		const widths = new Array(colCount).fill(0);

		function initWidths() {
			const available = Math.max(0, grid.clientWidth - actionsWidth);
			const base = Math.max(minW, available / colCount || minW);
			for (let i = 0; i < colCount; i++) {
				widths[i] = base;
			}
			// Fix any rounding drift to keep total == available.
			const sum = widths.reduce((a, b) => a + b, 0);
			if (available > 0 && sum !== available) {
				widths[colCount - 1] = Math.max(minW, widths[colCount - 1] + (available - sum));
			}
		}

		function applyWidths() {
			const cols = widths.map((w) => `${Math.max(minW, w)}px`);
			cols.push(`${actionsWidth}px`);
			grid.style.gridTemplateColumns = cols.join(" ");
		}

		function normalizeToAvailable() {
			const available = Math.max(0, grid.clientWidth - actionsWidth);
			const current = widths.reduce((a, b) => a + b, 0);
			if (available <= 0 || current <= 0) return;

			const scale = available / current;
			for (let i = 0; i < colCount; i++) {
				widths[i] = Math.max(minW, widths[i] * scale);
			}
			const sum = widths.reduce((a, b) => a + b, 0);
			if (sum !== available) {
				widths[colCount - 1] = Math.max(minW, widths[colCount - 1] + (available - sum));
			}
			applyWidths();
		}

		const resizeCells = Array.from(grid.querySelectorAll(".db-cell[data-col-index]"));
		resizeCells.forEach((cell) => {
			const idx = Number(cell.dataset.colIndex);
			if (Number.isNaN(idx) || idx < 0 || idx >= colCount) return;
			if (cell.querySelector(".db-resize-handle")) return;

			const handle = document.createElement("span");
			handle.className = "db-resize-handle";
			cell.appendChild(handle);

			handle.addEventListener("mousedown", (e) => {
				e.preventDefault();
				const startX = e.clientX;
				const startW = widths[idx];
				const nextIdx = idx + 1;
				if (nextIdx >= colCount) return;
				const nextW = widths[nextIdx];

				function onMove(ev) {
					const delta = ev.clientX - startX;
					let newW = Math.max(minW, startW + delta);
					let newNext = Math.max(minW, nextW - delta);
					// Clamp so total between the two stays constant.
					const total = startW + nextW;
					if (newW + newNext !== total) {
						const adjust = total - (newW + newNext);
						newNext = Math.max(minW, newNext + adjust);
						newW = Math.max(minW, total - newNext);
					}
					widths[idx] = newW;
					widths[nextIdx] = newNext;
					applyWidths();
				}

				function onUp() {
					document.removeEventListener("mousemove", onMove);
					document.removeEventListener("mouseup", onUp);
				}

				document.addEventListener("mousemove", onMove);
				document.addEventListener("mouseup", onUp);
			});
		});

		initWidths();
		applyWidths();

		if (typeof ResizeObserver !== "undefined") {
			const ro = new ResizeObserver(() => normalizeToAvailable());
			ro.observe(grid);
		} else {
			window.addEventListener("resize", normalizeToAvailable);
		}
	}

	window.addEventListener("load", () => {
		document.querySelectorAll(".db-grid").forEach(initGrid);
		const buttons = Array.from(document.querySelectorAll(".db-btn"));
		if (buttons.length > 0) {
			const max = Math.max(...buttons.map((b) => b.getBoundingClientRect().width));
			buttons.forEach((b) => {
				b.style.width = `${Math.ceil(max)}px`;
			});
		}
	});
})();
