import logging
import os
from datetime import datetime, timezone

from sql.psql_interface import PSQLInterface
from util.integrations.email.email_interface import render_template, send_email
from util.fcr.file_config_reader import FileConfigReader

interface = PSQLInterface()
fcr = FileConfigReader()


def _get_base_url() -> str:
	env_url = (os.environ.get("WEBSITE_BASE_URL") or os.environ.get("PUBLIC_BASE_URL") or "").strip()
	if env_url:
		return env_url
	try:
		conf = fcr.find("secrets.conf")
		if isinstance(conf, dict):
			for key in ("WEBSITE_BASE_URL", "PUBLIC_BASE_URL", "BASE_URL"):
				val = (conf.get(key) or "").strip()
				if val:
					return val
	except Exception:
		pass
	return "http://localhost:5000"

class UserManagement:
    @staticmethod
    def validate_registration_fields(
        referral_source: str,
        first_name: str,
        last_name: str,
        email: str,
        password: str,
        repeat_password: str,
    ) -> tuple[bool, str]:
        """Validate registration fields."""
        valid_referral_sources = {
            "friend",
            "github",
            "resume",
            "linkedin",
            "other",
        }

        if referral_source not in valid_referral_sources:
            return False, "Invalid referral source."

        if not first_name or not last_name:
            return False, "First and last name cannot be empty."

        if "@" not in email:
            return False, "Invalid email address."

        if len(password) < 8:
            return False, "Password must be at least 8 characters long."

        if password != repeat_password:
            return False, "Passwords do not match."

        status, message = interface.insert_pending_user({
            "referral_source": referral_source,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "password": password,
        })

        if not status:
            return status, message
        
        # In this branch, message is the verification token
        token = message
        base_url = _get_base_url().rstrip("/")
        verify_url = f"{base_url}/verify-email/{token}"
        expiry_text = "This link may be invalid due to a server error."
        try:
            token_hash = interface._hash_verification_token(token)
            rows, _ = interface.client.get_rows_with_filters(
                "pending_users",
                equalities={"verification_token_hash": token_hash},
                page_limit=1,
                page_num=0,
            )
            if rows:
                expires_at = rows[0].get("token_expires_at")
                if expires_at:
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    remaining = max(0, int((expires_at - now).total_seconds()))
                    if remaining <= 0:
                        expiry_text = "This link has expired."
                    elif remaining < 3600:
                        minutes = (remaining + 59) // 60
                        unit = "minute" if minutes == 1 else "minutes"
                        expiry_text = f"This link will expire in {minutes} {unit}."
                    else:
                        hours = (remaining + 3599) // 3600
                        unit = "hour" if hours == 1 else "hours"
                        expiry_text = f"This link will expire in {hours} {unit}."
        except Exception:
            pass

        body_html = render_template("verify_email.html", {
            "verify_url": verify_url,
            "expiry_text": expiry_text,
        })
        body_text = (
            "Someone has created an account with this email address. If this was you, "
            "click the button below to verify your email address.\n\n"
            f"Verification button: {verify_url}\n\n"
            f"{expiry_text}\n\n"
            "If you did not create this account, you can ignore this email.\n"
        )

        result = send_email(
            to_addrs=[email],
            subject="Verify your email",
            body_text=body_text,
            body_html=body_html,
        )
        if not result.ok:
            logging.warning("Failed to send verification email to %s: %s", email, result.error)
            return False, "We could not send a verification email. Please try again later."
        
        return True, "You will be redirected to the email verification page shortly."
    
    @staticmethod
    def login_user(
        email: str,
        password: str,
        remember_me: bool,
        ip: str,
        user_agent: str,
    ) -> tuple[bool, str]:
        """Login user with email and password."""
        return interface.login_user(
            email=email,
            password=password,
            remember_me=remember_me,
            ip=ip,
            user_agent=user_agent,
        )

    @staticmethod
    def get_user_by_session_token(session_token: str) -> dict | None:
        """Retrieve user by session token."""
        user = interface.check_session_token(session_token)
        if user:
            logging.info("Session token validated for %s", user["email"])
        return user
