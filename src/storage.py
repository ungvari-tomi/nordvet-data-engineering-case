"""SQLite storage for VetOne clinic and appointment records."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any


class SQLiteStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database_path)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS clinics (
                clinic_id TEXT PRIMARY KEY,
                name TEXT,
                raw_json TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                appointment_id TEXT PRIMARY KEY,
                clinic_id TEXT,
                scheduled_at TEXT,
                status TEXT,
                raw_json TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def upsert_clinics(self, records: Iterable[dict[str, Any]]) -> int:
        rows = []
        for record in records:
            clinic_id = record.get("id") or record.get("clinic_id")
            if clinic_id is None:
                raise ValueError("Clinic record is missing its id")
            rows.append(
                (
                    str(clinic_id),
                    record.get("name"),
                    self._serialize(record),
                )
            )
        with self._connection:
            self._connection.executemany(
                """
                INSERT INTO clinics (clinic_id, name, raw_json)
                VALUES (?, ?, ?)
                ON CONFLICT(clinic_id) DO UPDATE SET
                    name = excluded.name,
                    raw_json = excluded.raw_json
                """,
                rows,
            )
        return len(rows)

    def upsert_appointments(self, records: Iterable[dict[str, Any]]) -> int:
        rows = []
        for record in records:
            appointment_id = record.get("id")
            if appointment_id is None:
                raise ValueError("Appointment record is missing its id")
            rows.append(
                (
                    str(appointment_id),
                    record.get("clinic_id"),
                    record.get("scheduled_at"),
                    record.get("status"),
                    self._serialize(record),
                )
            )
        with self._connection:
            self._connection.executemany(
                """
                INSERT INTO appointments (
                    appointment_id, clinic_id, scheduled_at, status, raw_json
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(appointment_id) DO UPDATE SET
                    clinic_id = excluded.clinic_id,
                    scheduled_at = excluded.scheduled_at,
                    status = excluded.status,
                    raw_json = excluded.raw_json
                """,
                rows,
            )
        return len(rows)

    def refresh_appointments_full(self) -> int:
        return self._refresh_full_table(
            source_table="appointments",
            target_table="appointments_full",
            primary_key="id",
        )

    def refresh_clinics_full(self) -> int:
        return self._refresh_full_table(
            source_table="clinics",
            target_table="clinics_full",
            primary_key="id",
        )

    def _refresh_full_table(
        self,
        source_table: str,
        target_table: str,
        primary_key: str,
    ) -> int:
        records = [
            json.loads(row[0])
            for row in self._connection.execute(
                f"SELECT raw_json FROM {self._quote_identifier(source_table)}"
            )
        ]
        if any(not isinstance(record, dict) for record in records):
            raise ValueError(f"{source_table} raw_json values must contain JSON objects")
        if any(record.get(primary_key) is None for record in records):
            raise ValueError(
                f"{source_table} raw_json records must contain {primary_key}"
            )

        columns = sorted({key for record in records for key in record})
        if primary_key not in columns:
            columns.append(primary_key)

        definitions = []
        for column in columns:
            affinity = self._column_affinity(records, column)
            definition = f"{self._quote_identifier(column)} {affinity}"
            if column == primary_key:
                definition += " PRIMARY KEY"
            definitions.append(definition)

        quoted_columns = ", ".join(
            self._quote_identifier(column) for column in columns
        )
        placeholders = ", ".join("?" for _ in columns)
        rows = [
            tuple(self._sqlite_value(record.get(column)) for column in columns)
            for record in records
        ]
        with self._connection:
            quoted_target = self._quote_identifier(target_table)
            self._connection.execute(f"DROP TABLE IF EXISTS {quoted_target}")
            self._connection.execute(
                f"CREATE TABLE {quoted_target} ({', '.join(definitions)})"
            )
            if rows:
                self._connection.executemany(
                    f"INSERT INTO {quoted_target} ({quoted_columns}) "
                    f"VALUES ({placeholders})",
                    rows,
                )
        return len(rows)

    @classmethod
    def _column_affinity(
        cls, records: list[dict[str, Any]], column: str
    ) -> str:
        values = [
            record[column]
            for record in records
            if column in record and record[column] is not None
        ]
        if all(isinstance(value, (bool, int)) for value in values):
            return "INTEGER"
        if all(isinstance(value, (bool, int, float)) for value in values):
            return "REAL"
        return "TEXT"

    @classmethod
    def _sqlite_value(cls, value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return cls._serialize(value)
        if isinstance(value, bool):
            return int(value)
        return value

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    @staticmethod
    def _serialize(record: dict[str, Any]) -> str:
        return json.dumps(record, ensure_ascii=False, separators=(",", ":"))
