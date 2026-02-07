import logging
from datetime import datetime, timezone

from sql.psql_interface import PSQLInterface
from util.integrations.email.email_interface import render_template, send_email
from util.fcr.file_config_reader import FileConfigReader
from util.base_url import get_public_base_url
from util.verification_utils import build_verification_expiry_text

interface = PSQLInterface()
fcr = FileConfigReader()

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
        base_url = get_public_base_url(fcr=fcr).rstrip("/")
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
                expiry_text = build_verification_expiry_text(expires_at)
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
