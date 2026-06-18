import json
import os
from datetime import datetime

TRACKER_FILE = "data/entered_competitions.json"


def load_tracker() -> dict:
    if not os.path.exists(TRACKER_FILE):
        return {"entered": [], "last_updated": None}
    with open(TRACKER_FILE) as f:
        return json.load(f)


def save_tracker(data: dict):
    os.makedirs("data", exist_ok=True)
    data["last_updated"] = datetime.utcnow().isoformat()
    with open(TRACKER_FILE, "w") as f:
        json.dump(data, f, indent=2)


def is_already_entered(url: str) -> bool:
    tracker = load_tracker()
    return any(e.get("url") == url for e in tracker.get("entered", []))


def mark_as_entered(competition: dict):
    tracker = load_tracker()
    tracker.setdefault("entered", []).append(competition)
    save_tracker(tracker)


def get_stats() -> dict:
    tracker = load_tracker()
    entered = tracker.get("entered", [])
    return {"total_entered": len(entered), "last_updated": tracker.get("last_updated")}
