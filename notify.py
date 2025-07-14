import os
import requests
from msal import ConfidentialClientApplication
from dotenv import load_dotenv
from datetime import datetime
from sqlalchemy import create_engine, text

# Load environment variables
load_dotenv()

# Secrets and config
CLIENT_ID = os.getenv("CLIENT_ID")
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
GRAPH_USER = os.getenv("GRAPH_USER")
EMAIL_RECEIVER = os.getenv("NOTIFY_EMAIL")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:Vaibhav@localhost:5432/assetmanagement")

# Base URL (change to your deployed domain or IP)
BASE_URL = "http://apps.accrevent.com:3030"

# MS Graph auth
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPE = ["https://graph.microsoft.com/.default"]

# MSAL app
app = ConfidentialClientApplication(
    client_id=CLIENT_ID,
    client_credential=CLIENT_SECRET,
    authority=AUTHORITY
)

# DB connection
engine = create_engine(DATABASE_URL)


def get_token():
    token_response = app.acquire_token_for_client(scopes=SCOPE)
    if "access_token" in token_response:
        return token_response["access_token"]
    raise Exception("❌ Failed to acquire token: " + str(token_response))


def send_email_notification(data: dict):
    access_token = get_token()
    send_url = f"https://graph.microsoft.com/v1.0/users/{GRAPH_USER}/sendMail"

    # Admin recipients
    raw_admin_emails = EMAIL_RECEIVER or ""
    admin_emails = [email.strip() for email in raw_admin_emails.split(",") if email.strip()]
    user_email = None

    # 🔍 Fetch user email from laptop_details table using asset_id
    asset_id = data.get("asset_id")
    if asset_id:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT email FROM laptop_details WHERE asset_id = :aid"),
                {"aid": asset_id}
            ).fetchone()
            if result and result.email:
                user_email = result.email.strip()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Shared email body
    common_body = f"""
Ticket ID: {data.get('ticket_id')}
Asset ID: {data.get('asset_id')}
Description: {data.get('description')}
Name: {data.get('name')}
Make: {data.get('make')}
Model: {data.get('model')}
Serial Number: {data.get('serial_number')}
Time: {now_str}
"""

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # ------------------------------------------
    # ✉️ Send email to Admins (with admin link)
    # ------------------------------------------
    if admin_emails:
        admin_recipients = [{"emailAddress": {"address": email}} for email in admin_emails]
        admin_payload = {
            "message": {
                "subject": f"📩 New Ticket Submitted: {data.get('ticket_id')}",
                "body": {
                    "contentType": "Text",
                    "content": common_body + f"\nView Ticket: {BASE_URL}/admin/ticket/{data.get('ticket_id')}"
                },
                "toRecipients": admin_recipients
            }
        }

        admin_res = requests.post(send_url, headers=headers, json=admin_payload)
        if admin_res.status_code != 202:
            raise Exception(f"❌ Failed to send admin email: {admin_res.text}")

    # ------------------------------------------
    # ✉️ Send email to User (with user link)
    # ------------------------------------------
    if user_email:
        user_payload = {
            "message": {
                "subject": f"🛠️ Your Asset Ticket: {data.get('ticket_id')}",
                "body": {
                    "contentType": "Text",
                    "content": common_body + f"\nTrack Ticket: {BASE_URL}/ticket/{data.get('ticket_id')}"
                },
                "toRecipients": [{"emailAddress": {"address": user_email}}]
            }
        }

        user_res = requests.post(send_url, headers=headers, json=user_payload)
        if user_res.status_code != 202:
            raise Exception(f"❌ Failed to send user email: {user_res.text}")
