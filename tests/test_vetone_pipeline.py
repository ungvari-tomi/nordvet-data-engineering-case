from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch
from unittest.mock import Mock

from datetime import date

from src.dashboard_data import (
    get_available_dates,
    get_clinic_options,
    get_clinic_period_metrics,
    get_dimension_metrics,
    get_daily_clinic_metrics,
    get_period_trends,
    get_rolling_month_clinic_metrics,
)
from src.pipeline import _date_windows, _initial_load_dates, _progress_status
from src.storage import SQLiteStore
from src.vetone_client import VetOneAPIError, VetOneClient


class FakeResponse:
    def __init__(
        self,
        payload: Any,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


class VetOneClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = Mock()
        self.client = VetOneClient(
            "https://example.test",
            "client",
            "secret",
            session=self.session,
        )

    def tearDown(self) -> None:
        self.client.close()

    def test_fetches_token_and_follows_appointment_cursors(self) -> None:
        self.session.request.side_effect = [
            FakeResponse({"access_token": "test-token"}),
            FakeResponse(
                {
                    "data": [{"id": "apt-1", "status": "booked"}],
                    "next_cursor": "page-2",
                }
            ),
            FakeResponse(
                {"data": [{"id": "apt-2", "status": "no_show"}], "next_cursor": None}
            ),
        ]

        appointments = list(self.client.iter_appointments("2026-10-04", "2026-10-04"))

        self.assertEqual([item["id"] for item in appointments], ["apt-1", "apt-2"])
        first_get = self.session.request.call_args_list[1].kwargs
        second_get = self.session.request.call_args_list[2].kwargs
        self.assertEqual(first_get["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(first_get["params"]["from"], "2026-10-04")
        self.assertEqual(first_get["params"]["include_deleted"], "true")
        self.assertEqual(second_get["params"]["cursor"], "page-2")

    def test_rejects_repeated_pagination_cursor(self) -> None:
        self.session.request.side_effect = [
            FakeResponse({"access_token": "test-token"}),
            FakeResponse({"data": [], "next_cursor": "same"}),
            FakeResponse({"data": [], "next_cursor": "same"}),
        ]

        with self.assertRaisesRegex(VetOneAPIError, "repeated cursor"):
            list(self.client.iter_appointments("2026-10-04", "2026-10-04"))

    def test_retries_temporary_server_error_on_same_page(self) -> None:
        self.session.request.side_effect = [
            FakeResponse({"access_token": "test-token"}),
            FakeResponse({"error": "temporary error, please retry"}, status_code=500),
            FakeResponse({"data": [{"id": "apt-1"}], "next_cursor": None}),
        ]

        with patch("src.vetone_client.sleep") as sleep_mock:
            appointments = list(
                self.client.iter_appointments("2026-10-04", "2026-10-04")
            )

        self.assertEqual([item["id"] for item in appointments], ["apt-1"])
        first_attempt = self.session.request.call_args_list[1].kwargs
        retry_attempt = self.session.request.call_args_list[2].kwargs
        self.assertEqual(first_attempt["params"], retry_attempt["params"])
        sleep_mock.assert_called_once_with(1)

    def test_retries_rate_limit_after_retry_after_delay(self) -> None:
        self.session.request.side_effect = [
            FakeResponse({"access_token": "test-token"}),
            FakeResponse(
                {"error": "rate limited"},
                status_code=429,
                headers={"Retry-After": "3"},
            ),
            FakeResponse({"data": [{"id": "apt-1"}], "next_cursor": None}),
        ]

        with patch("src.vetone_client.sleep") as sleep_mock:
            appointments = list(
                self.client.iter_appointments("2026-10-04", "2026-10-04")
            )

        self.assertEqual([item["id"] for item in appointments], ["apt-1"])
        sleep_mock.assert_called_once_with(3)


class SQLiteStoreTests(unittest.TestCase):
    def test_refreshes_full_clinics_table_from_raw_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "nordvet.sqlite3"
            with SQLiteStore(database) as store:
                store.upsert_clinics(
                    [
                        {
                            "id": "CL-1",
                            "name": "Copenhagen",
                            "active": True,
                            "opened_on": "2020-01-01",
                        },
                        {
                            "id": "CL-2",
                            "name": "Malmö",
                            "active": False,
                        },
                    ]
                )

                self.assertEqual(store.refresh_clinics_full(), 2)

            connection = sqlite3.connect(database)
            try:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(clinics_full)"
                    )
                }
                rows = connection.execute(
                    """
                    SELECT id, name, active, opened_on
                    FROM clinics_full
                    ORDER BY id
                    """
                ).fetchall()
            finally:
                connection.close()

            self.assertEqual(columns, {"id", "name", "active", "opened_on"})
            self.assertEqual(
                rows,
                [
                    ("CL-1", "Copenhagen", 1, "2020-01-01"),
                    ("CL-2", "Malmö", 0, None),
                ],
            )

    def test_upserts_records_and_keeps_full_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "nested" / "vetone.sqlite3"
            with SQLiteStore(database) as store:
                self.assertEqual(
                    store.upsert_clinics([{"id": "CL-1", "name": "Copenhagen"}]),
                    1,
                )
                store.upsert_appointments(
                    [
                        {
                            "id": "apt-1",
                            "clinic_id": "CL-1",
                            "scheduled_at": "2026-10-04T09:30+02:00",
                            "status": "booked",
                            "patient_ref": "synthetic-ref",
                        }
                    ]
                )
                self.assertEqual(
                    store.upsert_appointments(
                        [
                            {
                                "id": "apt-1",
                                "clinic_id": "CL-1",
                                "scheduled_at": "2026-10-04T09:30+02:00",
                                "status": "no_show",
                                "patient_ref": "synthetic-ref",
                            }
                        ]
                    ),
                    1,
                )

            connection = sqlite3.connect(database)
            try:
                row = connection.execute(
                    """
                    SELECT status, raw_json
                    FROM appointments
                    WHERE appointment_id = 'apt-1'
                    """
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(row[0], "no_show")
            self.assertEqual(json.loads(row[1])["patient_ref"], "synthetic-ref")

    def test_refreshes_full_appointments_table_from_raw_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "nordvet.sqlite3"
            with SQLiteStore(database) as store:
                store.upsert_appointments(
                    [
                        {
                            "id": "apt-1",
                            "clinic_id": "CL-1",
                            "status": "completed",
                            "duration_minutes": 30,
                        },
                        {
                            "id": "apt-2",
                            "clinic_id": "CL-2",
                            "status": "no_show",
                            "patient_ref": "synthetic-ref",
                        },
                    ]
                )

                self.assertEqual(store.refresh_appointments_full(), 2)

            connection = sqlite3.connect(database)
            try:
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(appointments_full)")
                }
                rows = connection.execute(
                    """
                    SELECT id, clinic_id, status, duration_minutes, patient_ref
                    FROM appointments_full
                    ORDER BY id
                    """
                ).fetchall()
            finally:
                connection.close()

            self.assertEqual(
                columns,
                {"id", "clinic_id", "status", "duration_minutes", "patient_ref"},
            )
            self.assertEqual(
                rows,
                [
                    ("apt-1", "CL-1", "completed", 30, None),
                    ("apt-2", "CL-2", "no_show", None, "synthetic-ref"),
                ],
            )


class DateWindowTests(unittest.TestCase):
    def test_date_windows_are_adjacent_inclusive_and_cover_range(self) -> None:
        self.assertEqual(
            list(_date_windows("2026-09-28", "2026-10-04", 2)),
            [
                ("2026-09-28", "2026-09-29"),
                ("2026-09-30", "2026-10-01"),
                ("2026-10-02", "2026-10-03"),
                ("2026-10-04", "2026-10-04"),
            ],
        )

    def test_date_windows_reject_zero_days(self) -> None:
        with self.assertRaises(ValueError):
            list(_date_windows("2026-09-28", "2026-09-28", 0))

    def test_initial_load_starts_on_july_third_and_ends_today(self) -> None:
        self.assertEqual(
            _initial_load_dates(date(2026, 10, 5)),
            ("2026-07-03", "2026-10-05"),
        )

    def test_daily_windows_cover_initial_load_range_without_gaps(self) -> None:
        windows = list(_date_windows("2026-07-03", "2026-07-06", 1))
        self.assertEqual(
            windows,
            [
                ("2026-07-03", "2026-07-03"),
                ("2026-07-04", "2026-07-04"),
                ("2026-07-05", "2026-07-05"),
                ("2026-07-06", "2026-07-06"),
            ],
        )

    def test_progress_status_shows_bar_date_and_counts(self) -> None:
        self.assertEqual(
            _progress_status(1, 4, "2026-07-03", "2026-07-03"),
            "[#######-----------------------] 1/4 days | 2026-07-03",
        )


class DashboardDataTests(unittest.TestCase):
    def test_service_and_vet_breakdowns_follow_selected_period(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "nordvet.sqlite3"
            with SQLiteStore(database) as store:
                store.upsert_clinics([{"id": "CL-1", "name": "Clinic One"}])
                store.upsert_appointments(
                    [
                        {
                            "id": "apt-service-no-show",
                            "clinic_id": "CL-1",
                            "vet_id": "V1",
                            "service": "dental",
                            "scheduled_at": "2026-10-03T09:00+02:00",
                            "status": "no_show",
                        },
                        {
                            "id": "apt-service-completed",
                            "clinic_id": "CL-1",
                            "vet_id": "V1",
                            "service": "dental",
                            "scheduled_at": "2026-10-04T09:00+02:00",
                            "status": "completed",
                        },
                        {
                            "id": "apt-other-vet-no-show",
                            "clinic_id": "CL-1",
                            "vet_id": "V2",
                            "service": "consultation",
                            "scheduled_at": "2026-10-04T10:00+02:00",
                            "status": "no_show",
                        },
                        {
                            "id": "apt-cancelled",
                            "clinic_id": "CL-1",
                            "vet_id": "V1",
                            "service": "dental",
                            "scheduled_at": "2026-10-04T11:00+02:00",
                            "status": "cancelled",
                        },
                        {
                            "id": "apt-outside-month",
                            "clinic_id": "CL-1",
                            "vet_id": "V1",
                            "service": "dental",
                            "scheduled_at": "2026-09-30T09:00+02:00",
                            "status": "no_show",
                        },
                    ]
                )
                store.refresh_appointments_full()

            service_rows = get_dimension_metrics(
                database, "2026-10-04", "Monthly", "service"
            )
            vet_rows = get_dimension_metrics(
                database, "2026-10-04", "Monthly", "vet"
            )
            service_by_name = {row["category"]: row for row in service_rows}
            vet_by_id = {row["category"]: row for row in vet_rows}
            self.assertEqual(
                (
                    service_by_name["dental"]["no_shows"],
                    service_by_name["dental"]["completed"],
                    service_by_name["dental"]["no_show_rate"],
                ),
                (1, 1, 0.5),
            )
            self.assertEqual(
                (
                    vet_by_id["V1"]["no_shows"],
                    vet_by_id["V1"]["completed"],
                    vet_by_id["V1"]["no_show_rate"],
                ),
                (1, 1, 0.5),
            )
            self.assertNotIn("cancelled", service_by_name)
            with self.assertRaisesRegex(ValueError, "Unsupported breakdown"):
                get_dimension_metrics(database, "2026-10-04", "Weekly", "patient_ref")

    def test_rolling_month_clinic_metrics_use_trailing_30_days(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "nordvet.sqlite3"
            with SQLiteStore(database) as store:
                store.upsert_clinics(
                    [
                        {"id": "CL-1", "name": "Clinic One"},
                        {"id": "CL-2", "name": "Clinic Two"},
                    ]
                )
                store.upsert_appointments(
                    [
                        {
                            "id": "apt-in-range-no-show",
                            "clinic_id": "CL-1",
                            "scheduled_at": "2026-09-06T09:00+02:00",
                            "status": "no_show",
                        },
                        {
                            "id": "apt-in-range-completed",
                            "clinic_id": "CL-1",
                            "scheduled_at": "2026-10-05T09:00+02:00",
                            "status": "completed",
                        },
                        {
                            "id": "apt-outside-range",
                            "clinic_id": "CL-1",
                            "scheduled_at": "2026-09-05T09:00+02:00",
                            "status": "no_show",
                        },
                        {
                            "id": "apt-deleted",
                            "clinic_id": "CL-1",
                            "scheduled_at": "2026-10-04T09:00+02:00",
                            "status": "no_show",
                            "deleted_at": "2026-10-05T00:00:00Z",
                        },
                        {
                            "id": "apt-clinic-two",
                            "clinic_id": "CL-2",
                            "scheduled_at": "2026-10-01T09:00+02:00",
                            "status": "no_show",
                        },
                    ]
                )
                store.refresh_clinics_full()
                store.refresh_appointments_full()

            rows = get_rolling_month_clinic_metrics(database, "2026-10-05")
            clinic_one = next(row for row in rows if row["clinic_id"] == "CL-1")
            clinic_two = next(row for row in rows if row["clinic_id"] == "CL-2")
            self.assertEqual(
                (
                    clinic_one["no_shows"],
                    clinic_one["completed"],
                    clinic_one["outcomes"],
                    clinic_one["no_show_rate"],
                ),
                (1, 1, 2, 0.5),
            )
            self.assertEqual(
                (clinic_two["no_shows"], clinic_two["no_show_rate"]),
                (1, 1.0),
            )

    def test_daily_metrics_use_outcomes_and_exclude_deleted_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "nordvet.sqlite3"
            with SQLiteStore(database) as store:
                store.upsert_clinics(
                    [
                        {"id": "CL-1", "name": "Clinic One"},
                        {"id": "CL-2", "name": "Clinic Two"},
                    ]
                )
                store.upsert_appointments(
                    [
                        {
                            "id": "apt-no-show",
                            "clinic_id": "CL-1",
                            "scheduled_at": "2026-10-04T09:00+02:00",
                            "status": "no_show",
                        },
                        {
                            "id": "apt-completed",
                            "clinic_id": "CL-1",
                            "scheduled_at": "2026-10-04T10:00+02:00",
                            "status": "completed",
                        },
                        {
                            "id": "apt-cancelled",
                            "clinic_id": "CL-1",
                            "scheduled_at": "2026-10-04T11:00+02:00",
                            "status": "cancelled",
                        },
                        {
                            "id": "apt-booked",
                            "clinic_id": "CL-1",
                            "scheduled_at": "2026-10-04T12:00+02:00",
                            "status": "booked",
                        },
                        {
                            "id": "apt-deleted",
                            "clinic_id": "CL-1",
                            "scheduled_at": "2026-10-04T13:00+02:00",
                            "status": "no_show",
                            "deleted_at": "2026-10-05T00:00:00Z",
                        },
                    ]
                )
                store.refresh_clinics_full()
                store.refresh_appointments_full()

            self.assertEqual(get_available_dates(database), ["2026-10-04"])
            self.assertEqual(
                get_clinic_options(database),
                [
                    {"clinic_id": "CL-1", "clinic_name": "Clinic One"},
                    {"clinic_id": "CL-2", "clinic_name": "Clinic Two"},
                ],
            )
            metrics = get_daily_clinic_metrics(database, "2026-10-04")
            clinic_one = next(row for row in metrics if row["clinic_id"] == "CL-1")
            clinic_two = next(row for row in metrics if row["clinic_id"] == "CL-2")
            self.assertEqual(
                {
                    "no_shows": clinic_one["no_shows"],
                    "completed": clinic_one["completed"],
                    "cancelled": clinic_one["cancelled"],
                    "booked": clinic_one["booked"],
                    "outcomes": clinic_one["outcomes"],
                    "no_show_rate": clinic_one["no_show_rate"],
                },
                {
                    "no_shows": 1,
                    "completed": 1,
                    "cancelled": 1,
                    "booked": 1,
                    "outcomes": 2,
                    "no_show_rate": 0.5,
                },
            )
            self.assertEqual(clinic_two["outcomes"], 0)
            self.assertIsNone(clinic_two["no_show_rate"])

    def test_weekly_and_monthly_clinic_rankings_and_trends(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "nordvet.sqlite3"
            with SQLiteStore(database) as store:
                store.upsert_clinics(
                    [
                        {"id": "CL-1", "name": "Clinic One"},
                        {"id": "CL-2", "name": "Clinic Two"},
                    ]
                )
                store.upsert_appointments(
                    [
                        {
                            "id": "apt1",
                            "clinic_id": "CL-1",
                            "scheduled_at": "2026-09-30T09:00+02:00",
                            "status": "no_show",
                        },
                        {
                            "id": "apt2",
                            "clinic_id": "CL-1",
                            "scheduled_at": "2026-10-01T09:00+02:00",
                            "status": "completed",
                        },
                        {
                            "id": "apt3",
                            "clinic_id": "CL-1",
                            "scheduled_at": "2026-10-04T09:00+02:00",
                            "status": "no_show",
                        },
                        {
                            "id": "apt4",
                            "clinic_id": "CL-1",
                            "scheduled_at": "2026-10-05T09:00+02:00",
                            "status": "completed",
                        },
                        {
                            "id": "apt5",
                            "clinic_id": "CL-2",
                            "scheduled_at": "2026-10-02T09:00+02:00",
                            "status": "no_show",
                        },
                        {
                            "id": "apt6",
                            "clinic_id": "CL-2",
                            "scheduled_at": "2026-10-03T09:00+02:00",
                            "status": "completed",
                        },
                    ]
                )
                store.refresh_clinics_full()
                store.refresh_appointments_full()

            weekly = get_clinic_period_metrics(database, "2026-10-04", "Weekly")
            monthly = get_clinic_period_metrics(database, "2026-10-04", "Monthly")
            self.assertEqual(
                (weekly[0]["no_shows"], weekly[0]["completed"], weekly[0]["no_show_rate"]),
                (2, 1, 2 / 3),
            )
            self.assertEqual(
                (monthly[0]["no_shows"], monthly[0]["completed"], monthly[0]["no_show_rate"]),
                (1, 2, 1 / 3),
            )

            weekly_trends = get_period_trends(database, "Weekly")
            monthly_trends = get_period_trends(database, "Monthly")
            self.assertEqual(
                [(row["period_start"], row["no_shows"]) for row in weekly_trends],
                [("2026-09-28", 3), ("2026-10-05", 0)],
            )
            self.assertEqual(
                [(row["period_start"], row["no_shows"]) for row in monthly_trends],
                [("2026-09-01", 1), ("2026-10-01", 2)],
            )
            filtered_weekly = get_period_trends(database, "Weekly", "CL-1")
            self.assertEqual(
                [(row["period_start"], row["no_shows"]) for row in filtered_weekly],
                [("2026-09-28", 2), ("2026-10-05", 0)],
            )


if __name__ == "__main__":
    unittest.main()
