from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
DATE_KEYS = {"departure_date", "return_date"}


def _visit_dates(value: Any) -> list[date]:
    found: list[date] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in DATE_KEYS and isinstance(child, str):
                found.append(date.fromisoformat(child))
            else:
                found.extend(_visit_dates(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_visit_dates(child))
    return found


def _shift_dates(value: Any, offset: timedelta) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in DATE_KEYS and isinstance(child, str):
                value[key] = (date.fromisoformat(child) + offset).isoformat()
            else:
                _shift_dates(child, offset)
    elif isinstance(value, list):
        for child in value:
            _shift_dates(child, offset)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Move checked-in flight dates while preserving the gaps between them."
    )
    parser.add_argument(
        "--first-departure",
        type=date.fromisoformat,
        default=date.today() + timedelta(days=45),
        help="earliest date in YYYY-MM-DD form; defaults to 45 days from today",
    )
    args = parser.parse_args()

    documents: list[tuple[Path, Any]] = []
    all_dates: list[date] = []
    for path in sorted(EXAMPLES.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        documents.append((path, document))
        all_dates.extend(_visit_dates(document))

    if not all_dates:
        raise SystemExit("No example travel dates found.")

    offset = args.first_departure - min(all_dates)
    for path, document in documents:
        if not offset or not _visit_dates(document):
            continue
        _shift_dates(document, offset)
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        print(path.relative_to(EXAMPLES.parent))


if __name__ == "__main__":
    main()
