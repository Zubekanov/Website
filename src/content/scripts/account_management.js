document.addEventListener('DOMContentLoaded', () => {
	const authDropdown = document.getElementById('auth-dropdown');
	const sidePanel    = document.getElementById('side-panel');
	const closeBtn     = document.getElementById('close-panel');
	const loginForm    = document.getElementById('login-form');
	const regForm      = document.getElementById('register-form');

	// Hide dropdown and open side panel
	document.querySelectorAll('#auth-dropdown a').forEach(link => {
		link.addEventListener('click', e => {
			e.preventDefault();
			authDropdown.style.display = 'none';

			const action = link.dataset.action;
			loginForm.classList.toggle('active', action === 'login');
			regForm.classList.toggle('active', action === 'register');

			sidePanel.classList.add('open');
		});
	});

	// Close side panel
	closeBtn.addEventListener('click', () => {
		sidePanel.classList.remove('open');
		loginForm.classList.remove('active');
		regForm.classList.remove('active');
		clearMessage(loginForm);
		clearMessage(regForm);
	});

	// Close panel if clicking outside
	document.addEventListener('click', e => {
		if (!sidePanel.contains(e.target) && !document.getElementById('auth-menu').contains(e.target)) {
			sidePanel.classList.remove('open');
			authDropdown.style.display = '';
		}
	});

	// Prevent nav-item hover from reopening dropdown
	document.getElementById('auth-menu').addEventListener('mouseleave', () => {
		authDropdown.style.display = '';
	});

	// Utility: simple email format checker
	function isValidEmail(email) {
		const re = /^\S+@\S+\.\S+$/;
		return re.test(email);
	}

	// Handle Log In
	loginForm.querySelector('button').addEventListener('click', async e => {
		e.preventDefault();
		clearMessage(loginForm);

		const email    = loginForm.querySelector('input[type="email"]').value.trim();
		const password = loginForm.querySelector('input[type="password"]').value;

		// Frontend validation
		if (!email) {
			showMessage(loginForm, 'Email is required.', 'error');
			return;
		}
		if (!isValidEmail(email)) {
			showMessage(loginForm, 'Please enter a valid email address.', 'error');
			return;
		}
		if (!password) {
			showMessage(loginForm, 'Password is required.', 'error');
			return;
		}
		if (password.length < 8) {
			showMessage(loginForm, 'Password must be at least 8 characters.', 'error');
			return;
		}

		try {
			const res = await fetch('/login', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ email, password })
			});
			const data = await res.json();

			if (res.ok) {
				showMessage(loginForm, data.message || 'Logged in successfully.', 'success');
			} else {
				showMessage(loginForm, data.error || 'Login failed.', 'error');
			}
		} catch (err) {
			showMessage(loginForm, 'Email or Password are invalid.', 'error');
			console.error(err);
		}
	});

	// Handle Register
	regForm.querySelector('button').addEventListener('click', async e => {
		e.preventDefault();
		clearMessage(regForm);

		const username = regForm.querySelector('input[placeholder="Username"]').value.trim();
		const email    = regForm.querySelector('input[type="email"]').value.trim();
		const password = regForm.querySelector('input[type="password"]').value;

		// Frontend validation
		if (!username) {
			showMessage(regForm, 'Username is required.', 'error');
			return;
		}
		if (username.length < 4) {
			showMessage(regForm, 'Username must be at least 4 characters.', 'error');
			return;
		}
		if (!email) {
			showMessage(regForm, 'Email is required.', 'error');
			return;
		}
		if (!isValidEmail(email)) {
			showMessage(regForm, 'Please enter a valid email address.', 'error');
			return;
		}
		if (!password) {
			showMessage(regForm, 'Password is required.', 'error');
			return;
		}
		if (password.length < 8) {
			showMessage(regForm, 'Password must be at least 8 characters.', 'error');
			return;
		}

		try {
			const res = await fetch('/register', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ username, email, password })
			});
			const data = await res.json();

			if (res.ok) {
				showMessage(regForm, data.message || 'Registered successfully.', 'success');

			} else {
				showMessage(regForm, data.error || 'Registration failed.', 'error');
			}
		} catch (err) {
			showMessage(regForm, 'An unexpected error occurred.', 'error');
			console.error(err);
		}
	});

	// Utility: show a message at top of a form
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

	// Utility: clear any message
	function clearMessage(form) {
		const msg = form.querySelector('.message');
		if (msg) msg.remove();
	}
});

document.addEventListener("DOMContentLoaded", () => {
	const logoutBtn = document.getElementById("logout-btn");
	if (!logoutBtn) return;

	logoutBtn.addEventListener("click", async e => {
		e.preventDefault();
		try {
			const res = await fetch("/logout", {
				method: "GET",
				credentials: "include"
			});
			if (res.ok) {
				window.location.reload();
			} else {
				console.error("Logout failed:", await res.text());
			}
		} catch (err) {
			console.error("Error logging out:", err);
		}
	});
});

