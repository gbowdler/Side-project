import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
from bs4 import BeautifulSoup
from src.claude_agent import ask_claude

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
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


def _has_captcha(html: str) -> bool:
    html_lower = html.lower()
    return any(p in html_lower for p in CAPTCHA_PATTERNS)


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
    url = competition.get("url", "")
    html, page_text = _fetch_page(url)
    if not page_text:
        return False

    info = _entrant_info()
    prompt = f"""A competition page requires an email entry. Extract the entry details.

Return JSON only with keys:
- "to_email": the email address to send the entry to
- "subject": the email subject line required
- "body": the email body text to send

Entrant details to use where needed:
{info}

Competition page text:
{page_text}"""

    import json
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

Describe step-by-step Playwright actions to fill and submit the form. Return JSON only:
{{
  "actions": [
    {{"type": "fill", "selector": "css_selector", "value": "value_to_enter"}},
    {{"type": "click", "selector": "css_selector"}},
    {{"type": "select", "selector": "css_selector", "value": "option_value"}},
    {{"type": "check", "selector": "css_selector"}}
  ]
}}

Entrant details:
Name: {info['name']}
Email: {info['email']}
Phone: {info['phone']}
Address: {info['address_line1']}, {info['address_line2']}, {info['city']}, {info['postcode']}
Date of birth: {info['dob']}

Page text (use to identify form fields):
{page_text}"""

    import json
    raw = ask_claude(prompt)
    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return False
        plan = json.loads(match.group())
        actions = plan.get("actions", [])
    except (json.JSONDecodeError, AttributeError):
        return False

    if not actions:
        print("  No actions extracted")
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
                    print(f"  Timeout on action {atype} {selector} — continuing")
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

    if entry_type == "email":
        return enter_by_email(competition)
    elif entry_type == "web_form":
        return enter_by_web_form(competition)
    else:
        html, _ = _fetch_page(url)
        if not html:
            return False
        if _has_captcha(html):
            print("  CAPTCHA detected — skipping")
            return False
        return enter_by_web_form(competition)
