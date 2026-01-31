import logging
from sql.psql_interface import PSQLInterface

interface = PSQLInterface()

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
        # TODO: Email the verification token to the user
        print(f"Verification token for {email}: {message}")
        
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
