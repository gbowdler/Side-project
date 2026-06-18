import json
import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup
from src.claude_agent import ask_claude
from src.competition_finder import SOCIAL_MEDIA_DOMAINS

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

CAPTCHA_PATTERNS = [
    "recaptcha",
    "hcaptcha",
    "g-recaptcha",
    "captcha",
    "turnstile",
]


def _entrant_info() -> dict:
    return {
        "name": os.environ.get("ENTRANT_NAME", ""),
        "email": os.environ.get("GMAIL_ADDRESS", ""),
        "phone": os.environ.get("ENTRANT_PHONE", ""),
        "address_line1": os.environ.get("ENTRANT_ADDRESS_LINE1", ""),
        "address_line2": os.environ.get("ENTRANT_ADDRESS_LINE2", ""),
        "city": os.environ.get("ENTRANT_CITY", ""),
        "postcode": os.environ.get("ENTRANT_POSTCODE", ""),
        "dob": os.environ.get("ENTRANT_DOB", ""),
    }


def _is_social_media_url(url: str) -> bool:
    url_lower = url.lower()
    return any(domain in url_lower for domain in SOCIAL_MEDIA_DOMAINS)


def _has_captcha(html: str) -> bool:
    return any(p in html.lower() for p in CAPTCHA_PATTERNS)


def _fetch_page(url: str) -> tuple[str, str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        return resp.text, soup.get_text(separator="\n", strip=True)[:6000]
    except Exception as e:
        print(f"  Failed to fetch page: {e}")
        return "", ""


def enter_by_email(competition: dict) -> bool:
    info = _entrant_info()

    # Use pre-parsed email address if available (e.g. from Loquax direct parsing)
    to_email = competition.get("entry_email", "")
    title = competition.get("title", "")

    if to_email:
        subject = f"Competition Entry"
        body = f"Please enter me into this competition.\n\nName: {info['name']}\nEmail: {info['email']}"
        # Ask Claude to craft a better entry email using the title as context
        prompt = f"""Write a brief, natural competition entry email.

Competition: {title}
Entry email address: {to_email}
Entrant name: {info['name']}
Entrant email: {info['email']}

Return JSON only with keys: "subject" and "body". Keep body to 2-3 sentences max."""
        raw = ask_claude(prompt, max_tokens=300)
        try:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                subject = parsed.get("subject", subject)
                body = parsed.get("body", body)
        except (json.JSONDecodeError, AttributeError):
            pass
        return _send_email(to_email, subject, body)

    # Fall back to fetching the competition page and asking Claude to extract entry details
    url = competition.get("url", "")
    _, page_text = _fetch_page(url)
    if not page_text:
        return False

    prompt = f"""A competition page requires an email entry. Extract the entry details.

Return JSON only with keys:
- "to_email": the email address to send the entry to
- "subject": the email subject line required
- "body": the email body text to send

Entrant details:
Name: {info['name']}, Email: {info['email']}, Phone: {info['phone']}

Competition page text:
{page_text}"""

    raw = ask_claude(prompt)
    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return False
        entry = json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        return False

    to_email = entry.get("to_email", "")
    subject = entry.get("subject", "Competition Entry")
    body = entry.get("body", "")

    if not to_email or not body:
        print("  Could not extract email entry details")
        return False

    return _send_email(to_email, subject, body)


def _send_email(to_email: str, subject: str, body: str) -> bool:
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]

    msg = MIMEMultipart()
    msg["From"] = gmail_address
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_address, gmail_password)
            server.sendmail(gmail_address, to_email, msg.as_string())
        print(f"  Email sent to {to_email}")
        return True
    except Exception as e:
        print(f"  Failed to send email: {e}")
        return False


def enter_by_web_form(competition: dict) -> bool:
    url = competition.get("url", "")
    html, page_text = _fetch_page(url)

    if not html:
        return False

    if _has_captcha(html):
        print("  CAPTCHA detected — skipping")
        return False

    info = _entrant_info()
    prompt = f"""A competition web form needs to be filled in and submitted.

Return JSON only:
{{
  "actions": [
    {{"type": "fill", "selector": "css_selector", "value": "value_to_enter"}},
    {{"type": "click", "selector": "css_selector"}},
    {{"type": "select", "selector": "css_selector", "value": "option_value"}},
    {{"type": "check", "selector": "css_selector"}}
  ]
}}

Entrant details:
Name: {info['name']}, Email: {info['email']}, Phone: {info['phone']}
Address: {info['address_line1']}, {info['address_line2']}, {info['city']}, {info['postcode']}
Date of birth: {info['dob']}

Page text:
{page_text}"""

    raw = ask_claude(prompt)
    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return False
        actions = json.loads(match.group()).get("actions", [])
    except (json.JSONDecodeError, AttributeError):
        return False

    if not actions:
        print("  No form actions extracted")
        return False

    return _execute_playwright_actions(url, actions)


def _execute_playwright_actions(url: str, actions: list[dict]) -> bool:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=20000)
            page.wait_for_load_state("networkidle", timeout=10000)

            for action in actions:
                atype = action.get("type")
                selector = action.get("selector", "")
                value = action.get("value", "")
                try:
                    if atype == "fill":
                        page.fill(selector, value, timeout=5000)
                    elif atype == "click":
                        page.click(selector, timeout=5000)
                        page.wait_for_load_state("networkidle", timeout=8000)
                    elif atype == "select":
                        page.select_option(selector, value, timeout=5000)
                    elif atype == "check":
                        page.check(selector, timeout=5000)
                except PlaywrightTimeout:
                    print(f"  Timeout on {atype} {selector} — continuing")
                except Exception as e:
                    print(f"  Action failed ({atype} {selector}): {e}")

            browser.close()
        return True
    except Exception as e:
        print(f"  Playwright error: {e}")
        return False


def enter_competition(competition: dict) -> bool:
    url = competition.get("url", "")
    entry_type = competition.get("entry_type", "")
    title = competition.get("title", url)

    print(f"Entering: {title}")

    if _is_social_media_url(url):
        print("  Social media URL — skipping")
        return False

    if entry_type == "email":
        return enter_by_email(competition)
    elif entry_type == "web_form":
        return enter_by_web_form(competition)
    else:
        html, _ = _fetch_page(url)
        if not html or _has_captcha(html):
            print("  CAPTCHA detected or page unavailable — skipping")
            return False
        return enter_by_web_form(competition)
