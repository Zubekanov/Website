document.addEventListener('DOMContentLoaded', () => {
	const form = document.getElementById('password-reset-form');

	// Utility: simple email format checker
	function isValidEmail(email) {
		const re = /^\S+@\S+\.\S+$/;
		return re.test(email);
	}

	form.querySelector('button').addEventListener('click', async (e) => {
		e.preventDefault();
		clearMessage(form);
		const email = form.querySelector('input[type="email"]').value.trim();

		if (!email) {
			showMessage(form, 'Email is required.', 'error');
			return;
		}
		if (!isValidEmail(email)) {
			showMessage(form, 'Please enter a valid email address.', 'error');
			return;
		}

		try {
			const res = await fetch('/password-reset-request', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ email })
			});
			const data = await res.json();

			if (res.ok) {
				showMessage(form, data.message || 'Password reset link sent.', 'success');
			} else {
				showMessage(form, data.error || 'Failed to send reset link.', 'error');
			}
		} catch (err) {
			showMessage(form, 'An unexpected error occurred.', 'error');
			console.error(err);
		}
	});

	// Utility functions for showing/clearing messages
	function showMessage(form, text, type) {
		let msg = form.querySelector('.message');
		if (!msg) {
			msg = document.createElement('div');
			msg.className = 'message';
			form.prepend(msg);
		}
		msg.textContent = text;
		msg.classList.remove('error', 'success');
		msg.classList.add(type);
	}

	function clearMessage(form) {
		const msg = form.querySelector('.message');
		if (msg) msg.remove();
	}
});