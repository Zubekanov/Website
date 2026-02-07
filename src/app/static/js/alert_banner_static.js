document.addEventListener("DOMContentLoaded", () => {
	const staticBanners = document.querySelectorAll(".alert-banner.static");
	staticBanners.forEach(banner => {
		const interval = parseInt(banner.dataset.interval || "6000", 10);
		initStaticBanner(banner, interval);
	});
});

function initStaticBanner(banner, interval) {
	const messages = [...banner.querySelectorAll("[data-alert-message]")];
	if (messages.length <= 1) {
		if (messages[0]) {
			messages[0].classList.add("is-active");
		}
		return;
	}

	let index = 0;

	function showMessage(i) {
		messages.forEach((el, idx) => {
			if (idx === i) {
				el.classList.add("is-active");
			} else {
				el.classList.remove("is-active");
			}
		});
	}

	showMessage(index);

	setInterval(() => {
		index = (index + 1) % messages.length;
		showMessage(index);
	}, interval);
}
