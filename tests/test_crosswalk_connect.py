"""Opening the crosswalk store names the database when it cannot.

sqlite3 reports what went wrong, not which file. The console opens the file `CR_DATABASE` names,
so a path the service user cannot create, or a file that is not an SQLite database, stopped it
with an error that left the operator guessing which setting to correct.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from counterparty_resolver.store import crosswalk, migrations


def test_a_database_that_cannot_be_opened_is_named(tmp_path: Path) -> None:
    missing = tmp_path / "absent" / "crosswalk.db"
    with pytest.raises(sqlite3.OperationalError) as caught:
        crosswalk.connect(missing)
    assert f"database: {missing}" in getattr(caught.value, "__notes__", [])


def test_a_file_that_is_not_a_database_is_named(tmp_path: Path) -> None:
    not_sqlite = tmp_path / "crosswalk.db"
    not_sqlite.write_text("plain text, not an SQLite file\n" * 8, encoding="utf-8")
    with pytest.raises(sqlite3.DatabaseError) as caught:
        crosswalk.connect(not_sqlite)
    assert f"database: {not_sqlite}" in getattr(caught.value, "__notes__", [])


def test_a_database_that_opens_is_unaffected(tmp_path: Path) -> None:
    connection = crosswalk.connect(tmp_path / "crosswalk.db")
    try:
        assert connection.execute("SELECT 1").fetchone() == (1,)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "failure", [sqlite3.OperationalError, migrations.MigrationRefusedError, KeyboardInterrupt]
)
def test_a_failed_migration_closes_the_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[BaseException]
) -> None:
    """The caller never receives the connection when opening fails, so nothing else can close it."""
    handed: list[sqlite3.Connection] = []

    def failing_migrate(connection: sqlite3.Connection, *, target: int | None) -> None:
        handed.append(connection)
        raise failure("planted")

    monkeypatch.setattr(crosswalk, "migrate", failing_migrate)
    with pytest.raises(failure, match="planted"):
        crosswalk.connect(tmp_path / "crosswalk.db")
    (connection,) = handed
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")
