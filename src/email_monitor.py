import email
import imaplib
import os
import re
import smtplib
from email.mime.text import MIMEText

import requests
from src.claude_agent import ask_claude


def _get_imap_connection() -> imaplib.IMAP4_SSL:
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    imap.login(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
    return imap


def _fetch_unread_emails(imap: imaplib.IMAP4_SSL) -> list[dict]:
    imap.select("INBOX")
    _, msg_ids = imap.search(None, "UNSEEN")
    emails = []

    for msg_id in msg_ids[0].split():
        _, msg_data = imap.fetch(msg_id, "(RFC822)")
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    break
        else:
            body = msg.get_payload(decode=True).decode("utf-8", errors="replace")

        emails.append({
            "id": msg_id,
            "from": msg.get("From", ""),
            "subject": msg.get("Subject", ""),
            "body": body[:3000],
        })

    return emails


def _classify_email(email_data: dict) -> str:
    prompt = f"""Classify this email received by a competition entry account.

Categories:
- winner: the person has won a prize
- runner_up: near miss, runner up, not quite winner
- verification: requires clicking a link to verify email address
- entry_confirmation: confirms the competition entry was received
- newsletter: promotional / marketing email
- spam: spam or irrelevant
- unknown: cannot determine

Return only the category name, nothing else.

From: {email_data['from']}
Subject: {email_data['subject']}
Body:
{email_data['body']}"""

    return ask_claude(prompt, max_tokens=50).strip().lower()


def _send_win_notification(email_data: dict):
    notification_email = os.environ.get("NOTIFICATION_EMAIL", "")
    if not notification_email:
        return

    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]

    body = f"""You may have won a competition!

Original email:
From: {email_data['from']}
Subject: {email_data['subject']}

{email_data['body']}"""

    msg = MIMEText(body, "plain")
    msg["From"] = gmail_address
    msg["To"] = notification_email
    msg["Subject"] = f"COMPETITION WIN: {email_data['subject']}"

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_address, gmail_password)
            server.sendmail(gmail_address, notification_email, msg.as_string())
        print(f"Win notification sent to {notification_email}")
    except Exception as e:
        print(f"Failed to send win notification: {e}")


def _handle_verification(email_data: dict):
    urls = re.findall(r'https?://[^\s<>"]+', email_data["body"])
    verification_url = None

    for url in urls:
        if any(kw in url.lower() for kw in ["verify", "confirm", "activate", "validate"]):
            verification_url = url
            break

    if not verification_url and urls:
        prompt = f"""This email requires email verification. Which URL should be clicked to verify?
Return only the URL, nothing else.

Subject: {email_data['subject']}
URLs found: {urls[:10]}
Email body: {email_data['body'][:1000]}"""
        verification_url = ask_claude(prompt, max_tokens=200).strip()

    if verification_url and verification_url.startswith("http"):
        try:
            requests.get(verification_url, timeout=15)
            print(f"  Verification link clicked: {verification_url}")
        except Exception as e:
            print(f"  Failed to click verification link: {e}")
    else:
        print("  No verification URL found")


def _mark_as_read(imap: imaplib.IMAP4_SSL, msg_id: bytes):
    imap.store(msg_id, "+FLAGS", "\\Seen")


def check_and_process_emails():
    print("Connecting to Gmail IMAP...")
    try:
        imap = _get_imap_connection()
    except Exception as e:
        print(f"Failed to connect to IMAP: {e}")
        return

    emails = _fetch_unread_emails(imap)
    print(f"Found {len(emails)} unread emails")

    for email_data in emails:
        subject = email_data["subject"]
        sender = email_data["from"]
        print(f"Processing: '{subject}' from {sender}")

        category = _classify_email(email_data)
        print(f"  Classified as: {category}")

        if category == "winner":
            _send_win_notification(email_data)
        elif category == "runner_up":
            print("  Runner up email — logged")
        elif category == "verification":
            _handle_verification(email_data)
        elif category in ("entry_confirmation", "newsletter", "spam", "unknown"):
            print(f"  No action needed ({category})")

        _mark_as_read(imap, email_data["id"])

    imap.logout()
    print("Email check complete")
