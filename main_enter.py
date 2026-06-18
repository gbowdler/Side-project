import sys
from src.competition_finder import find_competitions
from src.competition_enterer import enter_competition
from src.tracker import is_already_entered, mark_as_entered, get_stats
from datetime import datetime, timezone


def main():
    print("Starting competition entry run...")

    competitions = find_competitions()

    entered_count = 0
    skipped_count = 0
    failed_count = 0

    for comp in competitions:
        url = comp.get("url", "")
        if not url:
            continue

        if is_already_entered(url):
            skipped_count += 1
            continue

        success = enter_competition(comp)

        if success:
            mark_as_entered({
                **comp,
                "entered_at": datetime.now(timezone.utc).isoformat(),
            })
            entered_count += 1
        else:
            failed_count += 1

    stats = get_stats()
    print(f"\nRun complete:")
    print(f"  Entered this run: {entered_count}")
    print(f"  Skipped (already entered): {skipped_count}")
    print(f"  Failed: {failed_count}")
    print(f"  Total all-time entered: {stats['total_entered']}")


if __name__ == "__main__":
    main()
