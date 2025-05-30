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

	// Handle Log In
	loginForm.querySelector('button').addEventListener('click', async e => {
		e.preventDefault();
		clearMessage(loginForm);

		const email    = loginForm.querySelector('input[type="email"]').value.trim();
		const password = loginForm.querySelector('input[type="password"]').value;

		try {
			const res = await fetch('/login', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ email, password })
			});
			const data = await res.json();

			if (res.ok) {
				showMessage(loginForm, data.message || 'Logged in successfully.', 'success');
				// optionally close the panel or redirect:
				// sidePanel.classList.remove('open');
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

		try {
			const res = await fetch('/register', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ username, email, password })
			});
			const data = await res.json();

			if (res.ok) {
				showMessage(regForm, data.message || 'Registered successfully.', 'success');
				// you might auto-switch to login form:
				// loginForm.classList.add('active');
				// regForm.classList.remove('active');
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
