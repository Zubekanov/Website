document.addEventListener("DOMContentLoaded", () => {
	const nav = document.querySelector(".navbar");
	if (!nav) return;
	const navCenter = nav.querySelector(".navbar__center");
	const navList = nav.querySelector(".nav-list");
	const mobileMq = window.matchMedia("(max-width: 900px)");
	const compactClasses = [
		"navbar--mobile-compact-1",
		"navbar--mobile-compact-2",
		"navbar--mobile-compact-3",
	];

	let openItem = null;

	const clearCompaction = () => {
		compactClasses.forEach((cls) => nav.classList.remove(cls));
	};

	const row2Overflowing = () => {
		if (!navCenter || !navList) return false;
		return navList.scrollWidth > navCenter.clientWidth + 1;
	};

	const applyMobileCompaction = () => {
		clearCompaction();
		if (!mobileMq.matches) return;
		if (!row2Overflowing()) return;
		for (const cls of compactClasses) {
			nav.classList.add(cls);
			if (!row2Overflowing()) return;
		}
	};

	const scheduleCompaction = () => {
		window.requestAnimationFrame(applyMobileCompaction);
	};

	nav.addEventListener("click", event => {
		const trigger = event.target.closest(".nav-link--trigger");
		if (!trigger) return;

		const item = trigger.closest(".nav-item--has-menu");
		if (!item) return;

		if (openItem && openItem !== item) {
			openItem.classList.remove("nav-item--open");
		}

		const isOpen = item.classList.toggle("nav-item--open");
		openItem = isOpen ? item : null;
	});

	document.addEventListener("click", event => {
		if (!openItem) return;
		if (event.target.closest(".nav-item--has-menu")) return;
		openItem.classList.remove("nav-item--open");
		openItem = null;
	});

	document.addEventListener("keydown", event => {
		if (event.key === "Escape" && openItem) {
			openItem.classList.remove("nav-item--open");
			openItem = null;
		}
	});

	if (typeof mobileMq.addEventListener === "function") {
		mobileMq.addEventListener("change", scheduleCompaction);
	} else if (typeof mobileMq.addListener === "function") {
		mobileMq.addListener(scheduleCompaction);
	}
	window.addEventListener("resize", scheduleCompaction, { passive: true });
	window.addEventListener("load", scheduleCompaction);
	if (document.fonts && document.fonts.ready && typeof document.fonts.ready.then === "function") {
		document.fonts.ready.then(scheduleCompaction).catch(() => {});
	}
	scheduleCompaction();
});
