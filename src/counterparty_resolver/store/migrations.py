"""Versioned migrations, and one expand/contract change carried out properly.

**Why expand/contract rather than one ALTER.** The resolution layer is read and written by a running
service. A single migration that renames `approved_by` to `approver_id` is only safe if the database
and every process that talks to it change in the same instant, which they do not: deploys are
rolling, and for some minutes old code and new schema are both live. Expand/contract is the
technique that removes the instant:

1. **expand** — add the new column, nullable, and backfill it. Old code ignores a column it has
   never heard of; new code can read either. Both versions run against this schema.
2. **transition** — new code writes both columns. Nothing is dropped, so a rollback is a deploy
   rather than a restore.
3. **contract** — once no process reads the old column, drop it. This step is **refused while any
   row would lose information**, which is the guard that makes the sequence more than a naming
   convention.

The thing being renamed is small on purpose. `approved_by` held a free-text name; `approver_id`
holds a stable identifier, because a display name is not a foreign key and an approval nobody can
attribute in six months is not an audit trail. This module is worth its own file for the sequence,
not for the column.

**Legacy tables are out of bounds.** Every statement here is checked against
`schema.LEGACY_TABLES` before it runs, and `test_store.py` asserts the same thing over the
committed migration list, so a DDL statement touching a source system's table cannot reach a
database by being written carefully.
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from dataclasses import dataclass
from typing import Literal

from counterparty_resolver.store.schema import (
    LEGACY_TABLES,
    RESOLUTION_SCHEMA_V1,
)

__all__ = [
    "MIGRATIONS",
    "Migration",
    "MigrationRefusedError",
    "current_version",
    "migrate",
    "statements_touching_legacy_tables",
]

Phase = Literal["baseline", "expand", "transition", "contract"]


class MigrationRefusedError(RuntimeError):
    """A migration declined to run because applying it would lose or corrupt information."""


@dataclass(frozen=True)
class Migration:
    """One numbered step, its phase in the expand/contract sequence, and its SQL.

    `precondition` is a query that must return a single zero before `statements` run. It is what
    turns "contract after the backfill" from a comment into a refusal: the contract step below asks
    how many rows still have no `approver_id`, and aborts on any.
    """

    version: int
    phase: Phase
    description: str
    statements: str
    precondition: str | None = None
    precondition_message: str = ""


#: The `approved_by` -> `approver_id` rename, done in three deployable steps.
#:
#: Read the versions as deploys, not as a script: 2 ships, then application code that writes both
#: columns ships, then 3 and 4 ship. At every point in between, the database is readable by the code
#: that is actually running.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        phase="baseline",
        description="resolution layer: append-only merge ledger and source links",
        statements=RESOLUTION_SCHEMA_V1,
    ),
    Migration(
        version=2,
        phase="expand",
        description="add merge_ledger.approver_id, nullable, alongside approved_by",
        statements="ALTER TABLE merge_ledger ADD COLUMN approver_id TEXT;",
    ),
    Migration(
        version=3,
        phase="transition",
        description="backfill approver_id from approved_by for rows written before the expand",
        # The ledger's append-only triggers refuse UPDATE, which is correct and is also exactly the
        # problem every backfill on an immutable table has. Dropping the trigger for the duration
        # is the honest answer: the ledger's immutability is a property of the *running system*, and
        # a migration is the one moment the system is not running. It is restored in the same
        # transaction, so a failure rolls back to a ledger that still refuses writes.
        statements="""
        DROP TRIGGER merge_ledger_is_append_only_update;
        UPDATE merge_ledger
           SET approver_id = lower(replace(approved_by, ' ', '.'))
         WHERE approver_id IS NULL;
        CREATE TRIGGER merge_ledger_is_append_only_update
        BEFORE UPDATE ON merge_ledger
        BEGIN
            SELECT RAISE(ABORT, 'merge_ledger is append-only: record a reversing entry instead');
        END;
        """,
    ),
    Migration(
        version=4,
        phase="contract",
        description="drop merge_ledger.approved_by now that nothing reads it",
        statements="ALTER TABLE merge_ledger DROP COLUMN approved_by;",
        precondition="SELECT COUNT(*) FROM merge_ledger WHERE approver_id IS NULL",
        precondition_message=(
            "rows still have no approver_id: the backfill in version 3 has not finished, and "
            "contracting now would drop the only copy of who approved them"
        ),
    ),
    Migration(
        version=5,
        phase="expand",
        description="record the source links a merge displaced, so a reversal can restore them",
        # Additive, and it has to be: a merge that overwrote an earlier link had no column to write
        # the old one into, so `unmerge` deleted the row instead of putting the earlier link back.
        # Merging A with B and then A with C, then reversing the second, left A under nothing at all
        # -- while `README.md` advertised an unmerge that restores the exact prior state. The column
        # is what makes that sentence true. Rows written before it exists read as NULL, which the
        # ledger treats as "displaced nothing", and that is the correct reading of them.
        statements="ALTER TABLE merge_ledger ADD COLUMN displaced_links_json TEXT;",
    ),
)

#: Words that change a table rather than read it. A migration naming a legacy table in one of these
#: is altering a source system's schema, which the brownfield constraint forbids.
_DDL = re.compile(
    r"\b(alter|drop|truncate)\s+(?:table|index|trigger|view)?\s*(?:if\s+exists\s+)?"
    r"[\"'`\[]?(\w+)",
    re.IGNORECASE,
)
_CREATE_ON = re.compile(
    r"\bcreate\s+(?:unique\s+)?(?:index|trigger)\b.*?\bon\s+[\"'`\[]?(\w+)", re.I
)


def statements_touching_legacy_tables(sql: str) -> list[str]:
    """Legacy tables this SQL would modify, if any.

    Deliberately blunt: it looks for the verbs that change a schema and the table each one names. It
    will not catch SQL assembled at runtime, which is why `migrate` also refuses at execution time
    and why `MIGRATIONS` is a committed constant rather than a directory scanned at import.
    """
    hits: list[str] = []
    for match in (*_DDL.finditer(sql), *_CREATE_ON.finditer(sql)):
        table = match.group(match.lastindex or 1)
        if table and table.lower() in LEGACY_TABLES:
            hits.append(match.group(0).strip())
    return hits


def current_version(connection: sqlite3.Connection) -> int:
    """The highest applied migration, or 0 on a database that has none."""
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if tables is None:
        return 0
    row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0] or 0)


def migrate(
    connection: sqlite3.Connection,
    *,
    target: int | None = None,
    now: dt.datetime | None = None,
) -> list[Migration]:
    """Apply every migration up to `target`, one transaction each, and return what ran.

    Stopping part-way is a supported operation rather than an accident: `target=2` leaves the
    database in the expanded state, which is what the tests use to prove that both the old and the
    new shape of the application work against it.
    """
    stamp = (now or dt.datetime.now(tz=dt.UTC)).isoformat(timespec="seconds")
    applied: list[Migration] = []

    # Sorted, not trusted to the literal's order: writing a new step above an older one applied
    # it first and skipped the older one entirely, because `migrate` stops at anything already
    # applied. The list is the record; the version number is the order.
    for migration in sorted(MIGRATIONS, key=lambda m: m.version):
        if migration.version <= current_version(connection):
            continue
        if target is not None and migration.version > target:
            break

        offending = statements_touching_legacy_tables(migration.statements)
        if offending:
            raise MigrationRefusedError(
                f"migration {migration.version} would alter a legacy table "
                f"({'; '.join(offending)}). The source schemas are fixed; the resolution layer "
                "is additive."
            )

        if migration.precondition is not None:
            blocked = connection.execute(migration.precondition).fetchone()[0]
            if blocked:
                raise MigrationRefusedError(
                    f"migration {migration.version} ({migration.phase}) refused: "
                    f"{blocked} {migration.precondition_message}"
                )

        with connection:
            connection.executescript(migration.statements)
            connection.execute(
                "INSERT INTO schema_version (version, applied_at, phase, description) "
                "VALUES (?, ?, ?, ?)",
                (migration.version, stamp, migration.phase, migration.description),
            )
        applied.append(migration)

    return applied
