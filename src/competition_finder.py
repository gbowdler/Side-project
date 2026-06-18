import json
import re

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

SOURCES = [
    {
        "name": "Loquax Email-In",
        "url": "https://www.loquax.co.uk/email.php",
        "entry_type": "email",
        "hint": "Each competition listing shows a prize description and an email address to send your entry to. Look for patterns like 'Email: xxx@xxx.com' or 'Send to: xxx@xxx.com'. The URL for each competition is the Loquax page URL itself or a linked competition page.",
    },
    {
        "name": "Loquax Online Forms",
        "url": "https://www.loquax.co.uk/online.php",
        "entry_type": "web_form",
        "hint": "Each competition listing shows a prize and a link to an external competition page where a web form can be filled in. Extract the external URL for each competition.",
    },
    {
        "name": "The Prize Finder",
        "url": "https://www.theprizefinder.com/competitions",
        "entry_type": "web_form",
        "hint": "Each competition listing shows a prize, closing date, and a link to enter. Only include competitions that can be entered via a web form — skip any that say Instagram, Facebook, Twitter, TikTok, or require following on social media.",
    },
]


def _is_social_media_url(url: str) -> bool:
    url_lower = url.lower()
    return any(domain in url_lower for domain in SOCIAL_MEDIA_DOMAINS)


def _fetch_page_text(url: str) -> str:
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
            )
            page.goto(url, timeout=20000)
            page.wait_for_load_state("networkidle", timeout=10000)
            # Remove clutter
            page.evaluate("""
                document.querySelectorAll('script, style, nav, footer, header, iframe, .ads, #ads').forEach(el => el.remove());
            """)
            text = page.inner_text("body")
            browser.close()
            return text[:10000]
    except Exception as e:
        print(f"  Failed to fetch {url}: {e}")
        return ""


def _extract_competitions(page_text: str, entry_type: str, source_name: str, hint: str) -> list[dict]:
    prompt = f"""You are parsing a UK competition listing page called "{source_name}".

{hint}

Extract all competitions from the text below and return a JSON array (nothing else) where each object has:
- "title": prize or competition description
- "url": the full URL to enter the competition (must start with http)
- "entry_type": "{entry_type}"
- "closing_date": closing date string if shown, else null
- "source": "{source_name}"

Rules:
- Only include entries with a valid http URL
- Skip any competition where the URL is a social media page (facebook, instagram, twitter, tiktok, youtube)
- Skip any competition that requires following on social media to enter
- If no competitions are found, return []

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
    seen_urls = set()

    for source in SOURCES:
        print(f"Fetching competitions from {source['name']}...")
        page_text = _fetch_page_text(source["url"])
        if not page_text:
            print(f"  No content retrieved")
            continue

        competitions = _extract_competitions(
            page_text, source["entry_type"], source["name"], source["hint"]
        )
        print(f"  Found {len(competitions)} competitions before filtering")

        for comp in competitions:
            url = comp.get("url", "")
            if not url or not url.startswith("http"):
                continue
            if _is_social_media_url(url):
                print(f"  Skipping social media URL: {url}")
                continue
            if url not in seen_urls:
                seen_urls.add(url)
                all_competitions.append(comp)

    print(f"Total unique enterable competitions found: {len(all_competitions)}")
    return all_competitions
