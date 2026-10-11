"""Fetch a date range from VetOne and upsert it into a local SQLite database."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from src.storage import SQLiteStore
from src.vetone_client import VetOneClient

INITIAL_LOAD_START = date(2026, 7, 3)


def _date_argument(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a date in YYYY-MM-DD format") from exc


def _default_yesterday() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def _initial_load_dates(current_date: date | None = None) -> tuple[str, str]:
    end_date = current_date or date.today()
    return INITIAL_LOAD_START.isoformat(), end_date.isoformat()


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("Must be a positive integer")
    return parsed


def _date_windows(
    start_date: str,
    end_date: str,
    interval_days: int,
) -> Iterator[tuple[str, str]]:
    if interval_days < 1:
        raise ValueError("interval_days must be a positive integer")
    current = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    while current <= end:
        window_end = min(current + timedelta(days=interval_days - 1), end)
        yield current.isoformat(), window_end.isoformat()
        current = window_end + timedelta(days=1)


def _progress_status(
    completed: int,
    total: int,
    window_start: str,
    window_end: str,
) -> str:
    width = 30
    filled = int(width * completed / total)
    bar = "#" * filled + "-" * (width - filled)
    date_range = (
        window_start if window_start == window_end else f"{window_start} to {window_end}"
    )
    return f"[{bar}] {completed}/{total} days | {date_range}"


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Fetch VetOne clinics and appointments into SQLite."
    )
    parser.add_argument(
        "--initial-load",
        action="store_true",
        help="Load daily from 2026-07-03 through today",
    )
    parser.add_argument("--from-date", type=_date_argument)
    parser.add_argument("--to-date", type=_date_argument)
    parser.add_argument(
        "--interval-days",
        type=_positive_int,
        help="Calendar days per API request window (default: 2; initial load: 1)",
    )
    parser.add_argument("--database", type=Path, default=Path("data/nordvet.sqlite3"))
    args = parser.parse_args()

    if args.initial_load and (
        args.from_date is not None
        or args.to_date is not None
        or args.interval_days is not None
    ):
        parser.error(
            "--initial-load sets its own date range and daily interval; "
            "do not combine it with --from-date, --to-date, or --interval-days."
        )

    if args.initial_load:
        from_date, to_date = _initial_load_dates()
        interval_days = 1
    else:
        default_date = _default_yesterday()
        from_date = args.from_date or default_date
        to_date = args.to_date or default_date
        interval_days = args.interval_days or 2

    if from_date > to_date:
        parser.error("--from-date must not be later than --to-date")

    client_id = os.environ.get("VETONE_CLIENT_ID")
    client_secret = os.environ.get("VETONE_CLIENT_SECRET")
    if not client_id or not client_secret:
        parser.error(
            "Set VETONE_CLIENT_ID and VETONE_CLIENT_SECRET in the environment "
            "or in a local .env file."
        )

    base_url = os.environ.get(
        "VETONE_API_BASE_URL",
        "https://vetone-api.kinddesert-e2c4fbdc.westeurope.azurecontainerapps.io",
    )
    with SQLiteStore(args.database) as store:
        with VetOneClient(base_url, client_id, client_secret) as client:
            clinic_count = store.upsert_clinics(client.get_clinics())
            clinic_full_count = store.refresh_clinics_full()
            appointment_count = 0
            full_count = 0
            windows = list(_date_windows(from_date, to_date, interval_days))
            try:
                for index, (window_start, window_end) in enumerate(windows, start=1):
                    sys.stderr.write(
                        "\r"
                        + _progress_status(
                            index - 1,
                            len(windows),
                            window_start,
                            window_end,
                        )
                        + " | loading"
                    )
                    sys.stderr.flush()
                    appointments = list(
                        client.iter_appointments(window_start, window_end)
                    )
                    appointment_count += store.upsert_appointments(appointments)
                    full_count = store.refresh_appointments_full()
                    sys.stderr.write(
                        "\r"
                        + _progress_status(
                            index,
                            len(windows),
                            window_start,
                            window_end,
                        )
                        + f" | saved {len(appointments)} appointments"
                    )
                    sys.stderr.flush()
            finally:
                sys.stderr.write("\n")
                sys.stderr.flush()

    print(
        f"Upserted {clinic_count} clinics and {appointment_count} appointments "
        f"for {from_date} through {to_date} in "
        f"{interval_days}-day windows; clinics_full has {clinic_full_count} rows "
        f"and appointments_full has {full_count} "
        f"rows in {args.database}."
    )


if __name__ == "__main__":
    main()
