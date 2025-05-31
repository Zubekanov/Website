import uuid
from util.config_reader import ConfigReader
from util.gmail_manager import GmailClient
from util.psql_manager import PSQLClient

from argon2 import PasswordHasher
import secrets
import datetime

psql = PSQLClient()

hasher = PasswordHasher(
    # Kratos Argon2 results:
    # MEMORY                  64.00MB
    # ITERATIONS              1
    # PARALLELISM             8
    # SALT LENGTH             16
    # KEY LENGTH              32
    # EXPECTED DURATION       500ms
    # EXPECTED DEVIATION      500ms
    # DEDICATED MEMORY        1.00GB
    time_cost=1,
    memory_cost=64 * 1024,  # 64 MB
    parallelism=8,
    salt_len=16,
    hash_len=32,
)

class UserManagement:
    _instance = None
    _initialised = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(UserManagement, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if UserManagement._initialised:
            return
        UserManagement._initialised = True

        schema = ConfigReader.get_sql("schema.sql")
        for statement in schema:
            psql.execute(query=statement)
    
    def clean_user_data(self):
        """
        Remove all expired cookies and verification requests.
        """
        # Remove expired verification requests
        psql.delete_rows_by_conditions(
            table="verification_requests",
            conditions={"expires_at": {"<": datetime.datetime.now()}}
        )
        
        # Remove expired cookies (assuming cookies have an expiry field)
        psql.delete_rows_by_conditions(
            table="cookies",
            conditions={"expires_at": {"<": datetime.datetime.now()}}
        )
    
    def get_user_by_uid(self, uid) -> dict:
        """
        Retrieve user information by user ID.
        """
        result = psql.get_rows_by_conditions(
            table="users",
            conditions={"uid": uid}
        )
        return result[0] if result else None
    
    def get_user_by_email(self, email: str) -> dict:
        """
        Retrieve user information by email.
        """
        result = psql.get_rows_by_conditions(
            table="users",
            conditions={"email": email}
        )
        return result[0] if result else None
    
    @staticmethod
    def apply_hash(string: str) -> str:
        """
        Apply Argon2 hashing to the given string.
        Ideally this should be threaded as it takes 5 seconds to hash a password.
        """
        return hasher.hash(string)
    
    def register_user(self, username:str, email: str, password: str) -> bool:
        """
        Register a new user with the given username, email, and password.
        """
        if self.get_user_by_email(email):
            return False
        
        psql.insert_row(
            table="users",
            data={
                "uid": str(uuid.uuid4()),
                "username": username,
                "email": email,
                "password_hash": UserManagement.apply_hash(password),
            }
        )
        self.send_verification_email(email)
        return True
    
    def send_verification_email(self, email: str) -> str:
        """
        Send a verification email to the user.
        """
        user = self.get_user_by_email(email)

        if not user:
            return "User not found"
        
        if user["is_verified"]:
            return "User already verified"
        
        expiry = datetime.datetime.now() + datetime.timedelta(hours=2)
        # Try to generate unique tokens for verification.
        # Loops on collision up to 3 times.
        max_attempts = 3
        for attempt in range(max_attempts):
            verify_token = secrets.token_urlsafe(32)

            try:
                # insert into verification_requests
                psql.insert_row(
                    table="verification_requests",
                    data={
                        "verify_token": verify_token,
                        "uid": user["uid"],
                        "expires_at": expiry
                    }
                )
                break
            except Exception as e:
                if attempt == max_attempts - 1:
                    raise RuntimeError("Could not generate unique verification tokens")
                continue

        verify_link = f"https://zubekanov.com/verify?token={verify_token}"

        email_template_path = ConfigReader.get_content_file("verification_email.html")
        with open(email_template_path, "r") as f:
            email_template = f.read()
        if not email_template:
            raise FileNotFoundError("Verification email template not found")
        email_content = email_template.format(
            username=user["username"],
            verify_link=verify_link,
        )

        plaintext_template_path = ConfigReader.get_content_file("verification_email.txt")
        with open(plaintext_template_path, "r") as f:
            plaintext_template = f.read()
        if not plaintext_template:
            raise FileNotFoundError("Verification email plaintext template not found")
        plaintext_content = plaintext_template.format(
            username=user["username"],
            verify_link=verify_link,
        )

        gmail_client = GmailClient()
        response = gmail_client.send_html(
            to_addr=email,
            subject="Email Verification",
            plain=plaintext_content,
            html=email_content
        )

        if not response:
            raise RuntimeError("Failed to send verification email")

        print(f"[DEBUG] Verification email sent to {email} with token {verify_token}")

        return "Verification email sent successfully"

    def verify_user(self, token:str) -> bool:
        """
        Verify a user by their email address.
        """
        request = psql.get_rows_by_conditions(
            table="verification_requests",
            conditions={"verify_token": token}
        )
        
        if not request:
            return False
        
        request = request[0]
        
        if request["expires_at"] < datetime.datetime.now():
            return False
        
        user = self.get_user_by_uid(request["uid"])
        if not user:
            return False
        
        # Mark user as verified.
        psql.update_rows_by_conditions(
            table="users",
            updates={"is_verified": True},
            conditions={"uid": user["uid"]},
        )
        
        # Delete verification requests for this user.
        psql.delete_rows_by_conditions(
            table="verification_requests",
            conditions={"uid": user["uid"]}
        )
        
        return True
    
    def invalidate_auth_token(self, token: str) -> None:
        """
        Invalidate an auth token, effectively logging out the user.
        """
        result = psql.delete_rows_by_conditions(
            table="auth_tokens",
            conditions={"token": token}
        )

    def get_auth_token(self, email: str, password: str) -> str:
        """
        Authenticate user by email and password, returning an auth token.
        """
        user = self.get_user_by_email(email)
        if not user:
            return None
        
        try:
            hasher.verify(user["password_hash"], password)
        except Exception as e:
            return None
        
        # Generate a new auth token
        auth_token = secrets.token_urlsafe(32)
        
        # Store the auth token in the database
        psql.insert_row(
            table="auth_tokens",
            data={
                "token": auth_token,
                "uid": user["uid"],
                "expires_at": datetime.datetime.now() + datetime.timedelta(days=7)
            }
        )
        
        return auth_token

    def get_user_by_auth_token(self, token: str) -> dict:
        """
        Retrieve user information by auth token.
        """
        result = psql.get_rows_by_conditions(
            table="auth_tokens",
            conditions={"token": token}
        )
        
        if not result:
            return None
        
        auth_token = result[0]
        
        if auth_token["expires_at"] < datetime.datetime.now():
            return None
        
        user = self.get_user_by_uid(auth_token["uid"])
        return user if user else None
    
    def _debug_wipe_users(self):
        """
        Debug method to wipe all users and their data.
        """
        psql.execute("TRUNCATE TABLE users RESTART IDENTITY CASCADE;")
        print("[DEBUG] All user data wiped.")