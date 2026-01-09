

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

        # TODO: Actually send verification email here.
        # Also change redirect to check email page.

        return False, "Input validation passed, but email verification is not implemented."

        return True, "Please check your email for a verification link."