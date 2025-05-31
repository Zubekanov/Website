document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('password-reset-form');
    const newPwdInput = document.getElementById('new-password');
    const confirmPwdInput = document.getElementById('confirm-password');
    const messageDiv = document.getElementById('reset-message');

    // Utility to extract the token from URL ?token=...
    function getTokenFromURL() {
        const params = new URLSearchParams(window.location.search);
        return params.get('token');
    }

    // Show a message (error or success) inside the form
    function showMessage(text, type) {
        messageDiv.textContent = text;
        messageDiv.classList.remove('error', 'success');
        messageDiv.classList.add(type);
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        messageDiv.textContent = '';

        const newPassword = newPwdInput.value.trim();
        const confirmPassword = confirmPwdInput.value.trim();
        const token = getTokenFromURL();

        // Basic front-end validation
        if (!newPassword || !confirmPassword) {
            showMessage('Both fields are required.', 'error');
            return;
        }
        if (newPassword.length < 8) {
            showMessage('Password must be at least 8 characters.', 'error');
            return;
        }
        if (newPassword !== confirmPassword) {
            showMessage('Passwords do not match.', 'error');
            return;
        }
        if (!token) {
            showMessage('Invalid or missing reset token.', 'error');
            return;
        }

        try {
            const res = await fetch('/reset-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token, new_password: newPassword })
            });

            let data;
            try {
                data = await res.json();
            } catch {
                data = null;
            }

            if (res.ok) {
                showMessage(data && data.message ? data.message : 'Password reset successfully.', 'success');
            } else {
                const errText = data && (data.error || data.message)
                    ? data.error || data.message
                    : (data ? JSON.stringify(data) : await res.text());
                showMessage(`Error ${res.status}: ${errText}`, 'error');
            }
        } catch (err) {
            console.error(err);
            showMessage('An unexpected error occurred.', 'error');
        }
    });
});

