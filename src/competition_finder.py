import re
import requests
from bs4 import BeautifulSoup
from src.claude_agent import ask_claude
import json

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
}

SOURCES = [
    {
        "name": "Loquax Email-In",
        "url": "https://www.loquax.co.uk/email.php",
        "entry_type": "email",
    },
    {
        "name": "Loquax Online Forms",
        "url": "https://www.loquax.co.uk/online.php",
        "entry_type": "web_form",
    },
    {
        "name": "The Prize Finder",
        "url": "https://www.theprizefinder.com/competitions",
        "entry_type": "web_form",
    },
]


def _fetch_text(url: str) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)[:8000]
    except Exception as e:
        print(f"Failed to fetch {url}: {e}")
        return ""


def _extract_competitions(page_text: str, entry_type: str, source_name: str) -> list[dict]:
    prompt = f"""You are parsing a competition listing page. Extract all competitions from the text below.

Return a JSON array (and nothing else) where each object has:
- "title": competition title/prize description
- "url": the competition entry URL (absolute URL)
- "entry_type": "{entry_type}"
- "closing_date": closing date string if visible, else null
- "source": "{source_name}"

Only include entries with a valid URL. If no competitions found, return [].

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
        page_text = _fetch_text(source["url"])
        if not page_text:
            continue

        competitions = _extract_competitions(page_text, source["entry_type"], source["name"])
        print(f"  Found {len(competitions)} competitions")

        for comp in competitions:
            url = comp.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_competitions.append(comp)

    print(f"Total unique competitions found: {len(all_competitions)}")
    return all_competitions
