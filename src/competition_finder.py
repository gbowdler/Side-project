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
        "parser": "loquax_online",
    },
    {
        "name": "The Prize Finder",
        "url": "https://www.theprizefinder.com/competitions",
        "entry_type": "web_form",
        "parser": "theprizefinder",
    },
]


def _is_social_media_url(url: str) -> bool:
    return any(d in url.lower() for d in SOCIAL_MEDIA_DOMAINS)


def _fetch_html(url: str) -> str:
    try:
        resp = requests.Session().get(url, headers=HEADERS, timeout=20)
        print(f"  HTTP {resp.status_code} ({len(resp.text)} chars)")
        if resp.status_code != 200:
            return ""
        return resp.text
    except Exception as e:
        print(f"  Fetch failed: {e}")
        return ""


def _log_html_sample(html: str, label: str):
    """Print a slice of raw HTML to help debug link/email patterns."""
    soup = BeautifulSoup(html, "lxml")
    body = soup.find("body")
    sample = str(body)[:2000] if body else html[:2000]
    print(f"  --- {label} HTML sample ---")
    print(sample)
    print(f"  --- end sample ---")


def _parse_loquax_email(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    competitions = []
    seen: set[str] = set()
    email_re = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")

    # Log all anchor hrefs to see what link patterns exist
    all_hrefs = [a.get("href", "") for a in soup.find_all("a", href=True)][:30]
    print(f"  Loquax Email-In — first 30 hrefs: {all_hrefs}")

    # Try mailto: links
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if href.lower().startswith("mailto:"):
            email = href[7:].split("?")[0].strip()
            if email and email not in seen and "loquax" not in email:
                seen.add(email)
                context = (link.parent or link).get_text(" ", strip=True)[:150]
                competitions.append({
                    "title": context or email,
                    "url": "https://www.loquax.co.uk/email.php",
                    "entry_type": "email",
                    "entry_email": email,
                    "closing_date": None,
                    "source": "Loquax Email-In",
                })

    # Fallback: bare email addresses anywhere in page text
    if not competitions:
        for m in email_re.finditer(soup.get_text()):
            email = m.group()
            if "loquax" in email or email in seen:
                continue
            seen.add(email)
            competitions.append({
                "title": email,
                "url": "https://www.loquax.co.uk/email.php",
                "entry_type": "email",
                "entry_email": email,
                "closing_date": None,
                "source": "Loquax Email-In",
            })

    print(f"  Loquax email parser: {len(competitions)} entries")
    return competitions


def _parse_loquax_online(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")

    # Log all external hrefs to see what link patterns exist
    external = [a["href"] for a in soup.find_all("a", href=True)
                if a["href"].startswith("http") and "loquax" not in a["href"]][:20]
    print(f"  Loquax Online — external links found: {external}")

    competitions = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if not href.startswith("http") or "loquax" in href:
            continue
        if _is_social_media_url(href) or href in seen:
            continue
        seen.add(href)
        title = link.get_text(strip=True) or (link.parent or link).get_text(" ", strip=True)[:100]
        competitions.append({
            "title": title[:150],
            "url": href,
            "entry_type": "web_form",
            "closing_date": None,
            "source": "Loquax Online Forms",
        })

    print(f"  Loquax online parser: {len(competitions)} entries")
    return competitions


def _parse_theprizefinder(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")

    # Log all external hrefs to understand the link structure
    external = [a["href"] for a in soup.find_all("a", href=True)
                if a["href"].startswith("http") and "theprizefinder" not in a["href"]][:20]
    print(f"  ThePrizeFinder — external links: {external}")

    # Log internal link patterns
    internal = [a["href"] for a in soup.find_all("a", href=True)
                if not a["href"].startswith("http")][:20]
    print(f"  ThePrizeFinder — internal links: {internal}")

    # Try all external links that aren't social media (competitions link out directly)
    competitions = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if not href.startswith("http"):
            continue
        if "theprizefinder" in href:
            continue
        if _is_social_media_url(href) or href in seen:
            continue
        seen.add(href)
        title = link.get_text(strip=True)
        if not title or len(title) < 3:
            title = (link.parent or link).get_text(" ", strip=True)[:100]
        competitions.append({
            "title": title[:150],
            "url": href,
            "entry_type": "web_form",
            "closing_date": None,
            "source": "The Prize Finder",
        })

    print(f"  ThePrizeFinder parser: {len(competitions)} entries")
    return competitions


def find_competitions() -> list[dict]:
    all_competitions = []
    seen_keys: set[str] = set()

    for source in SOURCES:
        name = source["name"]
        parser = source["parser"]
        print(f"Fetching competitions from {name}...")
        html = _fetch_html(source["url"])
        if not html:
            continue

        if parser == "loquax_email":
            competitions = _parse_loquax_email(html)
        elif parser == "loquax_online":
            competitions = _parse_loquax_online(html)
        elif parser == "theprizefinder":
            competitions = _parse_theprizefinder(html)
        else:
            competitions = []

        for comp in competitions:
            url = comp.get("url", "")
            if not url.startswith("http"):
                continue
            if _is_social_media_url(url):
                continue
            key = comp.get("entry_email", url)
            if key not in seen_keys:
                seen_keys.add(key)
                all_competitions.append(comp)

    print(f"Total unique enterable competitions: {len(all_competitions)}")
    return all_competitions
