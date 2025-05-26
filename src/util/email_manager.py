import base64
from email.message import EmailMessage
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config_reader import ConfigReader

# Obtain OAuth2 credentials (auto-refreshes and persists token.json)
creds = ConfigReader.get_credentials(
    client_secrets="credentials.json",
    token_file="token.json",
    scopes=["https://mail.google.com/"]
)

# Load email settings
email_config = ConfigReader.get_key_value_config("email.config")
server_address = email_config.get("EMAIL_ADDRESS")
alias_address = email_config.get("ALIAS_ADDRESS", server_address)
admin_address = email_config.get("ADMIN_ADDRESS")


def send_test_email():
    """
    Send a test email via the Gmail API using the configured alias.
    """
    msg = EmailMessage()
    msg.set_content("This is a test email from the Gmail API.")
    msg["To"] = admin_address
    msg["From"] = alias_address
    msg["Subject"] = "Gmail API Test Email"

    # Encode the message for the API
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    body = {"raw": raw}

    try:
        service = build("gmail", "v1", credentials=creds)
        sent = service.users().messages().send(userId="me", body=body).execute()
        print(f"Message Id: {sent['id']}")
        return sent
    except HttpError as error:
        print(f"Failed to send email: {error}")
        return None


if __name__ == "__main__":
    print(f"Server address: {server_address}")
    send_test_email()
