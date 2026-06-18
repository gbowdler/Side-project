import re
import urllib.parse

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

# Loquax navigation/category pages — not competition entries
LOQUAX_NAV_PATHS = {
    "/competitions/", "/competitions", "/compers/", "/blog/", "/forums/",
    "/lottery/", "/win-a-house.php", "/competition-sites.php",
}

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
        "url": "https://www.loquax.co.uk/competitions/local-entry.php",
        "entry_type": "email",
        "parser": "loquax_email",
    },
    {
        "name": "Loquax Online Forms",
        "url": "https://www.loquax.co.uk/competitions/",
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

# ThePrizeFinder titles that are navigation/UI elements, not real competitions
TPF_SKIP_TITLES = {
    "", "skip to main content", "home", "sign up free today!",
    "log into your account", "more top competitions", "sign up",
}


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


def _parse_loquax_email(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")

    all_hrefs = [a.get("href", "") for a in soup.find_all("a", href=True)][:30]
    print(f"  Loquax Email-In hrefs: {all_hrefs}")

    competitions = []
    seen: set[str] = set()

    # Strategy 1: mailto: links
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if href.lower().startswith("mailto:"):
            email = href[7:].split("?")[0].strip()
            if email and email not in seen and "loquax" not in email:
                seen.add(email)
                context = (link.parent or link).get_text(" ", strip=True)[:150]
                competitions.append({
                    "title": context or email,
                    "url": "https://www.loquax.co.uk/competitions/local-entry.php",
                    "entry_type": "email",
                    "entry_email": email,
                    "closing_date": None,
                    "source": "Loquax Email-In",
                })

    # Strategy 2: bare @ addresses in text
    email_re = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")
    for m in email_re.finditer(soup.get_text()):
        email = m.group()
        if "loquax" in email or email in seen:
            continue
        seen.add(email)
        competitions.append({
            "title": email,
            "url": "https://www.loquax.co.uk/competitions/local-entry.php",
            "entry_type": "email",
            "entry_email": email,
            "closing_date": None,
            "source": "Loquax Email-In",
        })

    # Strategy 3: obfuscated emails like "name [at] domain dot co dot uk"
    text = soup.get_text(" ", strip=True)
    obf_re = re.compile(
        r"([\w.+-]+)\s*[\[\(\{]?\s*at\s*[\]\)\}]?\s*([\w.-]+)\s*[\[\(\{]?\s*dot\s*[\]\)\}]?\s*([\w.]{2,})",
        re.IGNORECASE,
    )
    for m in obf_re.finditer(text):
        email = f"{m.group(1)}@{m.group(2)}.{m.group(3)}"
        if "loquax" in email.lower() or email in seen:
            continue
        seen.add(email)
        competitions.append({
            "title": email,
            "url": "https://www.loquax.co.uk/competitions/local-entry.php",
            "entry_type": "email",
            "entry_email": email,
            "closing_date": None,
            "source": "Loquax Email-In",
        })

    print(f"  Loquax email parser: {len(competitions)} entries")
    return competitions


def _parse_loquax_online(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")

    all_links = [(a.get("href", ""), a.get_text(strip=True)) for a in soup.find_all("a", href=True)][:30]
    print(f"  Loquax Online first 30 links: {all_links}")

    competitions = []
    seen: set[str] = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        title = link.get_text(strip=True)

        if not href.startswith("http"):
            continue

        # Skip loquax navigation and category pages
        try:
            path = urllib.parse.urlparse(href).path
        except Exception:
            continue
        if "loquax.co.uk" in href:
            if any(path == nav or path.startswith(nav) for nav in LOQUAX_NAV_PATHS):
                continue
            if not title or len(title) < 5:
                continue

        if _is_social_media_url(href) or href in seen:
            continue

        seen.add(href)
        competitions.append({
            "title": title[:150] or href,
            "url": href,
            "entry_type": "web_form",
            "closing_date": None,
            "source": "Loquax Online Forms",
        })

    print(f"  Loquax online parser: {len(competitions)} competitions")
    return competitions


def _parse_theprizefinder(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    BASE = "https://www.theprizefinder.com"

    # Log first 30 to confirm patterns
    all_links = [(a.get("href", ""), a.get_text(strip=True)[:60]) for a in soup.find_all("a", href=True)][:30]
    print(f"  ThePrizeFinder first 30 links: {all_links}")

    competitions = []
    seen: set[str] = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        title = link.get_text(strip=True)

        # ThePrizeFinder uses /link-track?id=NNN redirect links for competitions
        is_link_track = (
            (href.startswith("/link-track?") or
             (href.startswith(BASE) and "/link-track?" in href))
        )
        if is_link_track:
            full_url = href if href.startswith("http") else BASE + href
            if title and title.lower() not in TPF_SKIP_TITLES and full_url not in seen:
                seen.add(full_url)
                competitions.append({
                    "title": title[:150],
                    "url": full_url,
                    "entry_type": "web_form",
                    "closing_date": None,
                    "source": "The Prize Finder",
                })
            continue

        # Also capture any direct external competition links
        if href.startswith("http") and "theprizefinder" not in href:
            if not _is_social_media_url(href) and href not in seen and title:
                seen.add(href)
                competitions.append({
                    "title": title[:150],
                    "url": href,
                    "entry_type": "web_form",
                    "closing_date": None,
                    "source": "The Prize Finder",
                })

    print(f"  ThePrizeFinder parser: {len(competitions)} competitions")
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
