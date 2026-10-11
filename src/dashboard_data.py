"""Read-only queries and clinic-level attendance metrics for the dashboard."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def _connect_read_only(database_path: str | Path) -> sqlite3.Connection:
    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _deleted_record_filter(
    connection: sqlite3.Connection,
    table: str,
    alias: str | None = None,
) -> str:
    columns = {
        row[1]
        for row in connection.execute(
            f"PRAGMA table_info({_quote_identifier(table)})"
        )
    }
    if "deleted_at" not in columns:
        return "1 = 1"
    prefix = f"{alias}." if alias else ""
    return f"({prefix}deleted_at IS NULL OR {prefix}deleted_at = '')"


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def get_available_dates(database_path: str | Path) -> list[str]:
    with closing(_connect_read_only(database_path)) as connection:
        deleted_filter = _deleted_record_filter(connection, "appointments_full")
        rows = connection.execute(
            f"""
            SELECT DISTINCT substr(scheduled_at, 1, 10) AS appointment_date
            FROM appointments_full
            WHERE scheduled_at IS NOT NULL
              AND {deleted_filter}
            ORDER BY appointment_date
            """
        )
        return [row[0] for row in rows]


def get_clinic_options(database_path: str | Path) -> list[dict[str, str]]:
    with closing(_connect_read_only(database_path)) as connection:
        rows = connection.execute(
            "SELECT id, name FROM clinics_full ORDER BY name, id"
        )
        return [
            {"clinic_id": row[0], "clinic_name": row[1] or row[0]}
            for row in rows
        ]


def get_daily_clinic_metrics(
    database_path: str | Path,
    report_date: str,
) -> list[dict[str, Any]]:
    return get_clinic_period_metrics(database_path, report_date, "Daily")


def _period_bounds(period_date: str, period: str) -> tuple[str, str]:
    selected = date.fromisoformat(period_date)
    if period == "Daily":
        start = selected
        end = selected + timedelta(days=1)
    elif period == "Weekly":
        start = selected - timedelta(days=selected.weekday())
        end = start + timedelta(days=7)
    elif period == "Monthly":
        start = selected.replace(day=1)
        end = (
            date(start.year + 1, 1, 1)
            if start.month == 12
            else date(start.year, start.month + 1, 1)
        )
    else:
        raise ValueError(f"Unsupported reporting period: {period}")
    return start.isoformat(), end.isoformat()


def get_clinic_period_metrics(
    database_path: str | Path,
    period_date: str,
    period: str,
) -> list[dict[str, Any]]:
    start_date, end_date = _period_bounds(period_date, period)
    return _get_clinic_metrics_for_date_range(
        database_path,
        start_date,
        end_date,
    )


def get_rolling_month_clinic_metrics(
    database_path: str | Path,
    end_date: str,
) -> list[dict[str, Any]]:
    last_day = date.fromisoformat(end_date)
    start_date = (last_day - timedelta(days=29)).isoformat()
    exclusive_end_date = (last_day + timedelta(days=1)).isoformat()
    return _get_clinic_metrics_for_date_range(
        database_path,
        start_date,
        exclusive_end_date,
    )


def _get_clinic_metrics_for_date_range(
    database_path: str | Path,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    with closing(_connect_read_only(database_path)) as connection:
        deleted_filter = _deleted_record_filter(
            connection, "appointments_full", "a"
        )
        rows = connection.execute(
            f"""
            SELECT
                c.id AS clinic_id,
                c.name AS clinic_name,
                SUM(CASE WHEN a.status = 'no_show' THEN 1 ELSE 0 END) AS no_shows,
                SUM(CASE WHEN a.status = 'completed' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN a.status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
                SUM(CASE WHEN a.status = 'booked' THEN 1 ELSE 0 END) AS booked
            FROM clinics_full AS c
            LEFT JOIN appointments_full AS a
                ON a.clinic_id = c.id
                AND substr(a.scheduled_at, 1, 10) >= ?
                AND substr(a.scheduled_at, 1, 10) < ?
                AND {deleted_filter}
            GROUP BY c.id, c.name
            ORDER BY c.name, c.id
            """,
            (start_date, end_date),
        )
        clinic_metrics = []
        for row in rows:
            no_shows, completed, cancelled, booked = (
                int(value or 0) for value in row[2:]
            )
            outcomes = no_shows + completed
            clinic_metrics.append(
                {
                    "clinic_id": row[0],
                    "clinic_name": row[1],
                    "no_shows": no_shows,
                    "completed": completed,
                    "cancelled": cancelled,
                    "booked": booked,
                    "outcomes": outcomes,
                    "no_show_rate": no_shows / outcomes if outcomes else None,
                }
            )
        return clinic_metrics


def get_dimension_metrics(
    database_path: str | Path,
    period_date: str,
    period: str,
    dimension: str,
) -> list[dict[str, Any]]:
    column_by_dimension = {
        "service": "service",
        "vet": "vet_id",
    }
    try:
        column = column_by_dimension[dimension]
    except KeyError as exc:
        raise ValueError(f"Unsupported breakdown dimension: {dimension}") from exc

    start_date, end_date = _period_bounds(period_date, period)
    with closing(_connect_read_only(database_path)) as connection:
        deleted_filter = _deleted_record_filter(connection, "appointments_full")
        rows = connection.execute(
            f"""
            SELECT
                COALESCE(NULLIF({column}, ''), 'Unknown') AS category,
                SUM(CASE WHEN status = 'no_show' THEN 1 ELSE 0 END) AS no_shows,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed
            FROM appointments_full
            WHERE substr(scheduled_at, 1, 10) >= ?
              AND substr(scheduled_at, 1, 10) < ?
              AND {deleted_filter}
              AND status IN ('no_show', 'completed')
            GROUP BY category
            ORDER BY category
            """,
            (start_date, end_date),
        )
        metrics = []
        for category, no_shows, completed in rows:
            no_shows = int(no_shows or 0)
            completed = int(completed or 0)
            outcomes = no_shows + completed
            metrics.append(
                {
                    "category": category,
                    "no_shows": no_shows,
                    "completed": completed,
                    "outcomes": outcomes,
                    "no_show_rate": no_shows / outcomes if outcomes else None,
                }
            )
        return metrics


def get_period_trends(
    database_path: str | Path,
    period: str,
    clinic_id: str | None = None,
) -> list[dict[str, Any]]:
    if period == "Weekly":
        period_expression = """
            date(
                substr(scheduled_at, 1, 10),
                '-' || ((CAST(strftime('%w', scheduled_at) AS INTEGER) + 6) % 7)
                    || ' days'
            )
        """
    elif period == "Monthly":
        period_expression = "substr(scheduled_at, 1, 7) || '-01'"
    else:
        raise ValueError(f"Unsupported trend period: {period}")

    with closing(_connect_read_only(database_path)) as connection:
        deleted_filter = _deleted_record_filter(connection, "appointments_full")
        clinic_filter = "AND clinic_id = ?" if clinic_id is not None else ""
        parameters: tuple[str, ...] = (
            (clinic_id,) if clinic_id is not None else ()
        )
        rows = connection.execute(
            f"""
            SELECT
                {period_expression} AS period_start,
                SUM(CASE WHEN status = 'no_show' THEN 1 ELSE 0 END) AS no_shows,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed
            FROM appointments_full
            WHERE scheduled_at IS NOT NULL
              AND {deleted_filter}
              AND status IN ('no_show', 'completed')
              {clinic_filter}
            GROUP BY period_start
            ORDER BY period_start
            """,
            parameters,
        )
        trends = []
        for period_start, no_shows, completed in rows:
            no_shows = int(no_shows or 0)
            completed = int(completed or 0)
            outcomes = no_shows + completed
            trends.append(
                {
                    "period_start": period_start,
                    "no_shows": no_shows,
                    "completed": completed,
                    "outcomes": outcomes,
                    "no_show_rate": no_shows / outcomes if outcomes else None,
                }
            )
        return trends
