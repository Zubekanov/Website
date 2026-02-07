document.addEventListener("DOMContentLoaded", () => {
	const nav = document.querySelector(".navbar");
	if (!nav) return;

	let openItem = null;

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
});
