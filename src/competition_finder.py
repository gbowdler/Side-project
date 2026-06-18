import json
import re

import requests
from bs4 import BeautifulSoup

from src.claude_agent import ask_claude

SOCIAL_MEDIA_DOMAINS = [
    "facebook.com", "fb.com",
    "instagram.com",
    "twitter.com", "x.com",
    "tiktok.com",
    "youtube.com",
    "pinterest.com",
    "snapchat.com",
    "linkedin.com",
]

# No 'br' — requests can't decode Brotli, causing garbled responses
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

SOURCES = [
    {
        "name": "Loquax Email-In",
        "url": "https://www.loquax.co.uk/email.php",
        "entry_type": "email",
        "parser": "loquax_email",
    },
    {
        "name": "Loquax Online Forms",
        "url": "https://www.loquax.co.uk/online.php",
        "entry_type": "web_form",
        "parser": "claude",
        "hint": "Each row is a competition with a prize description and a link to an external entry page. Extract the external URL for each.",
    },
    {
        "name": "The Prize Finder",
        "url": "https://www.theprizefinder.com/competitions",
        "entry_type": "web_form",
        "parser": "theprizefinder",
    },
]


def _is_social_media_url(url: str) -> bool:
    url_lower = url.lower()
    return any(domain in url_lower for domain in SOCIAL_MEDIA_DOMAINS)


def _fetch_html(url: str) -> str:
    try:
        session = requests.Session()
        resp = session.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        print(f"  HTTP {resp.status_code} ({len(resp.text)} chars)")
        if resp.status_code != 200:
            return ""
        return resp.text
    except Exception as e:
        print(f"  Failed to fetch {url}: {e}")
        return ""


def _html_to_text(html: str, limit: int = 20000) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "iframe"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    print(f"  Text length after stripping: {len(text)} chars (sending first {min(limit, len(text))})")
    return text[:limit]


def _parse_loquax_email(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    competitions = []
    seen_emails: set[str] = set()
    email_re = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")

    # Try mailto: links first — Loquax often uses these
    for link in soup.find_all("a", href=email_re):
        href = link.get("href", "")
        email_match = email_re.search(href)
        if not email_match:
            continue
        email = email_match.group()
        if email in seen_emails or "loquax" in email:
            continue
        seen_emails.add(email)
        # Grab surrounding context for the title
        parent_text = (link.parent or link).get_text(separator=" ", strip=True)[:150]
        competitions.append({
            "title": parent_text or email,
            "url": "https://www.loquax.co.uk/email.php",
            "entry_type": "email",
            "entry_email": email,
            "closing_date": None,
            "source": "Loquax Email-In",
        })

    # Fallback: scan table rows for bare email addresses
    if not competitions:
        for row in soup.find_all("tr"):
            row_text = row.get_text(separator=" ", strip=True)
            emails = email_re.findall(row_text)
            for email in emails:
                if "loquax" in email or email in seen_emails:
                    continue
                seen_emails.add(email)
                competitions.append({
                    "title": row_text[:120],
                    "url": "https://www.loquax.co.uk/email.php",
                    "entry_type": "email",
                    "entry_email": email,
                    "closing_date": None,
                    "source": "Loquax Email-In",
                })

    print(f"  Loquax email parser found {len(competitions)} entries (mailto links + table scan)")
    return competitions


def _parse_theprizefinder(html: str) -> list[dict]:
    """Extract competition links directly from ThePrizeFinder HTML."""
    soup = BeautifulSoup(html, "lxml")
    competitions = []
    seen_urls: set[str] = set()

    # ThePrizeFinder competition links go to /competition/<slug>
    for link in soup.find_all("a", href=re.compile(r"theprizefinder\.com/competition/")):
        url = link.get("href", "")
        if not url.startswith("http"):
            url = "https://www.theprizefinder.com" + url
        if url in seen_urls or _is_social_media_url(url):
            continue
        seen_urls.add(url)
        title = link.get_text(strip=True) or url
        competitions.append({
            "title": title,
            "url": url,
            "entry_type": "web_form",
            "closing_date": None,
            "source": "The Prize Finder",
        })

    # Also check relative links like /competition/<slug>
    if not competitions:
        for link in soup.find_all("a", href=re.compile(r"^/competition/")):
            url = "https://www.theprizefinder.com" + link.get("href", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            title = link.get_text(strip=True) or url
            competitions.append({
                "title": title,
                "url": url,
                "entry_type": "web_form",
                "closing_date": None,
                "source": "The Prize Finder",
            })

    print(f"  ThePrizeFinder parser found {len(competitions)} competition links")
    return competitions


def _claude_extract(page_text: str, entry_type: str, source_name: str, hint: str) -> list[dict]:
    prompt = f"""You are parsing a UK competition listing page called "{source_name}".

{hint}

Extract all competitions and return a JSON array (nothing else) where each object has:
- "title": prize or competition description
- "url": the full URL to enter the competition (must start with http)
- "entry_type": "{entry_type}"
- "closing_date": closing date string if shown, else null
- "source": "{source_name}"

Rules:
- Only include entries with a valid http URL
- Skip any competition where the URL is a social media page
- If no competitions found, return []

Page text:
{page_text}"""

    raw = ask_claude(prompt, max_tokens=4096)
    try:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        pass
    return []


def find_competitions() -> list[dict]:
    all_competitions = []
    seen_keys: set[str] = set()

    for source in SOURCES:
        name = source["name"]
        url = source["url"]
        entry_type = source["entry_type"]
        parser = source.get("parser", "claude")

        print(f"Fetching competitions from {name}...")
        html = _fetch_html(url)
        if not html:
            continue

        if parser == "loquax_email":
            competitions = _parse_loquax_email(html)
        elif parser == "theprizefinder":
            competitions = _parse_theprizefinder(html)
        else:
            page_text = _html_to_text(html)
            competitions = _claude_extract(page_text, entry_type, name, source.get("hint", ""))

        print(f"  {len(competitions)} competitions before filtering")

        for comp in competitions:
            comp_url = comp.get("url", "")
            if not comp_url or not comp_url.startswith("http"):
                continue
            if _is_social_media_url(comp_url):
                print(f"  Skipping social media: {comp_url}")
                continue
            dedup_key = comp.get("entry_email", comp_url)
            if dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                all_competitions.append(comp)

    print(f"Total unique enterable competitions: {len(all_competitions)}")
    return all_competitions
