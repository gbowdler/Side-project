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

# Realistic browser headers to avoid blocks
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
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
        "parser": "claude",
        "hint": "Each competition listing shows a prize, closing date, and entry link. Skip social media giveaways (Instagram, Facebook, Twitter, TikTok).",
    },
    {
        "name": "MSE Competitions",
        "url": "https://forums.moneysavingexpert.com/categories/competition-time",
        "entry_type": "web_form",
        "parser": "claude",
        "hint": "This is a forum where users post links to competitions. Extract competition titles and their external entry URLs. Skip social media.",
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
            print(f"  Non-200 response — content preview: {resp.text[:200]}")
            return ""
        return resp.text
    except Exception as e:
        print(f"  Failed to fetch {url}: {e}")
        return ""


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "iframe"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    print(f"  Text preview: {text[:300]}")
    return text[:10000]


# Direct HTML parser for Loquax email-in page
# Loquax lists competitions in a table: prize | email address | closing date
def _parse_loquax_email(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    competitions = []
    email_re = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

    # Each competition is typically a table row
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        row_text = row.get_text(separator=" ", strip=True)
        emails = email_re.findall(row_text)
        if not emails:
            continue
        # Skip header rows
        if any(h in row_text.lower() for h in ["email", "prize", "closing", "date"]):
            if len(row_text) < 60:
                continue
        competitions.append({
            "title": cells[0].get_text(strip=True)[:120],
            "url": "https://www.loquax.co.uk/email.php",
            "entry_type": "email",
            "entry_email": emails[0],
            "closing_date": cells[-1].get_text(strip=True) if len(cells) > 2 else None,
            "source": "Loquax Email-In",
        })

    # Fallback: scan entire page for email addresses with surrounding context
    if not competitions:
        text = soup.get_text(separator="\n", strip=True)
        for match in email_re.finditer(text):
            start = max(0, match.start() - 80)
            context = text[start: match.end() + 40].strip()
            if "@loquax" in match.group() or "@example" in match.group():
                continue
            competitions.append({
                "title": context[:100],
                "url": "https://www.loquax.co.uk/email.php",
                "entry_type": "email",
                "entry_email": match.group(),
                "closing_date": None,
                "source": "Loquax Email-In",
            })

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
- Skip any that requires following on social media
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
    seen_urls: set[str] = set()

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
        else:
            page_text = _html_to_text(html)
            competitions = _claude_extract(page_text, entry_type, name, source.get("hint", ""))

        print(f"  Extracted {len(competitions)} competitions before filtering")

        for comp in competitions:
            comp_url = comp.get("url", "")
            if not comp_url or not comp_url.startswith("http"):
                continue
            if _is_social_media_url(comp_url):
                print(f"  Skipping social media: {comp_url}")
                continue
            # For email-in comps the URL is the listing page — dedupe by email instead
            dedup_key = comp.get("entry_email", comp_url)
            if dedup_key not in seen_urls:
                seen_urls.add(dedup_key)
                all_competitions.append(comp)

    print(f"Total unique enterable competitions: {len(all_competitions)}")
    return all_competitions
