"""The brownfield layer's guarantees, asserted rather than described.

Four claims are load-bearing and each has a test that fails when the claim stops being true:

1. the legacy schemas are never altered;
2. the merge ledger is append-only in the database, not by convention;
3. a merge is idempotent under replay;
4. an unmerge restores the **exact** prior state, including source-system linkage.

(4) is asserted against a full snapshot of every table, taken before the merge and compared after
the unmerge. Asserting that `unmerge` ran, or that the resolved entity disappeared, would pass
against an implementation that left `source_link` rows pointing nowhere — which is the failure that
actually costs money, because downstream systems join on those rows.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

import pytest

from counterparty_resolver.store import crosswalk, ledger, migrations
from counterparty_resolver.store.schema import LEGACY_SCHEMA, LEGACY_TABLES, legacy_fingerprint

GLEIF: list[dict[str, Any]] = [
    {
        "lei": "5493001KJTIIGC8Y1R12",
        "legal_name": "BLOOMBERG FINANCE L.P.",
        "country": "GB",
        "city": "London",
        "postal_code": "EC4N 6AF",
        "address_line": "3 Queen Victoria Street",
        "registered_at": "RA000585",
        "registered_as": "01234567",
        "registration_status": "ISSUED",
        "successor_lei": None,
        "other_names": [("PREVIOUS_LEGAL_NAME", "Bloomberg Finance Limited")],
    },
    {
        "lei": "213800QILIUR11PB4H30",
        "legal_name": "Nordic Capital Sp. z o.o.",
        "country": "PL",
        "city": "Warszawa",
        "postal_code": "00-838",
        "address_line": "Prosta 51",
        "registered_at": "RA000549",
        "registered_as": "0000943675",
        "registration_status": "DUPLICATE",
        "successor_lei": None,
        "other_names": [],
    },
]

COMPANIES_HOUSE: list[dict[str, Any]] = [
    {
        "company_number": "01234567",
        "company_name": "BLOOMBERG FINANCE LIMITED",
        "company_status": "Active",
        "country_of_origin": "United Kingdom",
        "post_town": "LONDON",
        "post_code": "EC4N 6AF",
        "address_line_1": "3 Queen Victoria Street",
        "previous_name_1": "BLOOMBERG FINANCE L.P.",
        "previous_name_2": "BLOOMBERG LP UK",
    }
]

MERGE: dict[str, Any] = {
    "idempotency_key": "m-1",
    "left": ("gleif", "5493001KJTIIGC8Y1R12"),
    "right": ("companies_house", "01234567"),
    "resolved_id": "cp-1",
    "decision": "match",
    "score": 0.97,
    "evidence": [{"feature": "identifier_agreement", "detail": "RA000585 / 01234567"}],
    "approver_id": "ada.l",
}


@pytest.fixture
def db() -> sqlite3.Connection:
    connection = crosswalk.connect()
    crosswalk.seed(
        connection,
        gleif=[dict(r) for r in GLEIF],
        companies_house=[dict(r) for r in COMPANIES_HOUSE],
    )
    return connection


def _snapshot(connection: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of every table, ordered, so two states can be compared for equality."""
    connection.row_factory = None
    tables = [
        str(name)
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {
        table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall())  # noqa: S608
        for table in sorted(tables)
    }


def _statements_by_table(ddl: str) -> dict[str, str]:
    """`CREATE TABLE x (...)` statements keyed by table name."""
    out: dict[str, str] = {}
    for statement in (s.strip() for s in ddl.split(";")):
        if match := re.match(r"CREATE TABLE (\w+)", statement, re.IGNORECASE):
            out[match.group(1)] = statement
    return out


# ------------------------------------------------------------------ the brownfield constraint


def test_no_committed_migration_touches_a_legacy_table() -> None:
    """The blueprint's constraint: source schemas are fixed and the resolution layer is additive."""
    offending = {
        migration.version: hits
        for migration in migrations.MIGRATIONS
        if (hits := migrations.statements_touching_legacy_tables(migration.statements))
    }

    assert not offending, f"migrations altering a legacy table: {offending}"


def test_the_legacy_schema_in_a_migrated_database_is_the_one_that_was_declared(
    db: sqlite3.Connection,
) -> None:
    """Asserted against what SQLite reports, not against the constant the DDL was built from.

    Per table rather than over a concatenation, so a failure names the table that drifted instead of
    printing two digests and leaving the reader to work out which.
    """
    declared = {
        name: legacy_fingerprint(sql)
        for name, sql in _statements_by_table(LEGACY_SCHEMA).items()
        if name in LEGACY_TABLES
    }
    live = {
        str(name): legacy_fingerprint(str(sql))
        for name, sql in db.execute(
            # S608: the IN list is placeholders, and the values are bound below.
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name IN "  # noqa: S608
            f"({','.join('?' * len(LEGACY_TABLES))})",
            LEGACY_TABLES,
        )
    }

    assert live == declared, "a legacy table in the database is not the one that was declared"


def test_a_migration_that_alters_a_legacy_table_is_refused(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard, exercised. A rule nobody has seen reject anything is not a rule."""
    bad = migrations.Migration(
        version=99,
        phase="expand",
        description="planted breach: widen a source system's table",
        statements="ALTER TABLE legacy_gleif ADD COLUMN resolved_id TEXT;",
    )
    assert migrations.statements_touching_legacy_tables(bad.statements)
    monkeypatch.setattr(migrations, "MIGRATIONS", (*migrations.MIGRATIONS, bad))

    with pytest.raises(migrations.MigrationRefusedError, match="legacy table"):
        migrations.migrate(db)

    columns = {row[1] for row in db.execute("PRAGMA table_info(legacy_gleif)")}
    assert "resolved_id" not in columns, "the refusal has to happen before the statement runs"


# ---------------------------------------------------------------------- expand / contract


def _old_code_writes(connection: sqlite3.Connection, key: str) -> None:
    """A row as a process deployed before the expand writes it: `approved_by` and nothing else."""
    connection.execute(
        "INSERT INTO merge_ledger (idempotency_key, action, left_source, left_id, right_source, "
        "right_id, resolved_id, decision, evidence_json, approved_by, recorded_at) "
        "VALUES (?, 'merge', 'gleif', 'a', 'gleif', 'b', 'r', 'match', '[]', 'Ada L', 'now')",
        (key,),
    )
    connection.commit()


def test_the_database_is_usable_at_every_step_of_the_rename() -> None:
    """Old shape and new shape both work against the expanded schema. That is the point of it."""
    expanded = crosswalk.connect(target=2)
    columns = {row[1] for row in expanded.execute("PRAGMA table_info(merge_ledger)")}

    assert {"approved_by", "approver_id"} <= columns, (
        "the expand step must add the new column without removing the old one, or a rolling "
        "deploy has a window where the running code cannot read the table"
    )
    _old_code_writes(expanded, "old-code")  # the old shape still writes successfully

    contracted = crosswalk.connect()
    columns = {row[1] for row in contracted.execute("PRAGMA table_info(merge_ledger)")}
    assert "approver_id" in columns
    assert "approved_by" not in columns


def test_the_backfill_fills_rows_that_predate_the_expand() -> None:
    """Version 3 exists to make version 4 safe, and this is the thing it has to have done."""
    db = crosswalk.connect(target=2)
    _old_code_writes(db, "before-the-backfill")

    migrations.migrate(db, target=3)

    assert db.execute("SELECT approver_id FROM merge_ledger").fetchone()[0] == "ada.l"


def test_the_contract_step_refuses_while_a_row_would_lose_its_approver() -> None:
    """The window expand/contract exists for: old code still writing after the backfill ran.

    Versions 3 and 4 are separate deploys, and between them a process deployed before the expand can
    still insert a row carrying `approved_by` alone. Dropping the column then would destroy the only
    record of who approved that merge. The precondition is what turns "wait until nothing writes the
    old column" from a line in a runbook into a refusal.
    """
    db = crosswalk.connect(target=3)
    _old_code_writes(db, "written-during-the-rollout")

    with pytest.raises(migrations.MigrationRefusedError, match="approver_id"):
        migrations.migrate(db, target=4)

    assert migrations.current_version(db) == 3, "a refused migration must not half-apply"
    assert "approved_by" in {row[1] for row in db.execute("PRAGMA table_info(merge_ledger)")}


# ------------------------------------------------------------------------- the merge ledger


def test_the_ledger_refuses_update_and_delete_in_the_database(db: sqlite3.Connection) -> None:
    """Enforced by a trigger, so code that forgets the property still cannot violate it."""
    entry = ledger.merge(db, **MERGE)

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute(
            "UPDATE merge_ledger SET decision = 'no_match' WHERE entry_id = ?",
            (entry["entry_id"],),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("DELETE FROM merge_ledger WHERE entry_id = ?", (entry["entry_id"],))


def test_replaying_a_merge_changes_nothing(db: sqlite3.Connection) -> None:
    """The approval queue will retry after a timeout. Twice must mean once."""
    first = ledger.merge(db, **MERGE)
    after_first = _snapshot(db)

    second = ledger.merge(db, **MERGE)

    assert second["entry_id"] == first["entry_id"]
    assert _snapshot(db) == after_first


def test_unmerge_restores_the_exact_prior_state_including_source_linkage(
    db: sqlite3.Connection,
) -> None:
    """The committed unmerge test the blueprint asks for, compared against a full snapshot."""
    before = _snapshot(db)
    assert ledger.resolved_id_for(db, "gleif", "5493001KJTIIGC8Y1R12") is None

    entry = ledger.merge(db, **MERGE)
    assert ledger.links_for(db, "cp-1") == [
        ("companies_house", "01234567"),
        ("gleif", "5493001KJTIIGC8Y1R12"),
    ]

    ledger.unmerge(
        db,
        idempotency_key="u-1",
        reverses_entry_id=int(entry["entry_id"]),
        approver_id="ada.l",
        reason="the company number belongs to a subsidiary",
    )

    after = _snapshot(db)
    assert after["source_link"] == before["source_link"], (
        "source-system linkage was not restored; downstream systems join on these rows"
    )
    assert ledger.resolved_id_for(db, "gleif", "5493001KJTIIGC8Y1R12") is None
    assert ledger.links_for(db, "cp-1") == []

    # Every table is back as it was except the ledger, which grew by exactly the two entries.
    assert {t: rows for t, rows in after.items() if t != "merge_ledger"} == {
        t: rows for t, rows in before.items() if t != "merge_ledger"
    }
    assert [row[2] for row in sorted(after["merge_ledger"])] == ["merge", "unmerge"]


def test_reversing_a_merge_restores_a_link_it_displaced(db: sqlite3.Connection) -> None:
    """ "Restores the prior state" has to mean the prior state, not the empty one.

    Merge A with B, then A with C, then reverse the second. The first version deleted A's link and
    left it under no entity at all, because the merge that moved A had nowhere to record where A had
    come from. `migrations.py` version 5 is that column, and this is the test it exists for.
    """
    first = ledger.merge(
        db,
        idempotency_key="m-1",
        left=("gleif", "A"),
        right=("gleif", "B"),
        resolved_id="cp-1",
        decision="match",
        score=0.9,
        evidence=[],
        approver_id="ada.l",
    )
    after_first = _snapshot(db)

    moved = ledger.merge(
        db,
        idempotency_key="m-2",
        left=("gleif", "A"),
        right=("gleif", "C"),
        resolved_id="cp-2",
        decision="match",
        score=0.9,
        evidence=[],
        approver_id="ada.l",
    )
    assert ledger.resolved_id_for(db, "gleif", "A") == "cp-2"

    ledger.unmerge(
        db,
        idempotency_key="u-2",
        reverses_entry_id=int(moved["entry_id"]),
        approver_id="ada.l",
        reason="A belongs with B",
    )

    assert ledger.resolved_id_for(db, "gleif", "A") == "cp-1", (
        "A was moved from cp-1 and the reversal has to put it back, not unlink it"
    )
    assert ledger.links_for(db, "cp-1") == [("gleif", "A"), ("gleif", "B")]
    assert ledger.links_for(db, "cp-2") == []
    assert _snapshot(db)["source_link"] == after_first["source_link"], (
        "every source link is back exactly as the first merge left it"
    )
    assert int(first["entry_id"]) == int(
        db.execute(
            "SELECT linked_by_entry_id FROM source_link WHERE source = 'gleif' AND source_id = 'A'"
        ).fetchone()[0]
    ), "and it is attributed to the merge that actually made it"


def test_the_migration_list_is_in_version_order() -> None:
    """The literal is the record of what happened, and reading it out of order applies it wrongly.

    Version 5 was first written above version 4 in the tuple. `migrate` stops at anything already
    applied, so it ran 1, 2, 3, 5 and skipped 4 entirely -- the contract step never happened and the
    next insert failed on a NOT NULL column nobody had dropped. `migrate` now sorts; this keeps the
    file readable as history as well.
    """
    versions = [m.version for m in migrations.MIGRATIONS]

    assert versions == sorted(versions), f"migrations are declared out of order: {versions}"
    assert len(set(versions)) == len(versions), f"duplicate migration versions: {versions}"


def test_unmerge_refuses_to_undo_the_middle_of_a_chain(db: sqlite3.Connection) -> None:
    """Repairing a later merge silently would be this function deciding what nobody approved."""
    first = ledger.merge(db, **MERGE)
    ledger.merge(
        db,
        idempotency_key="m-2",
        left=("gleif", "5493001KJTIIGC8Y1R12"),
        right=("gleif", "213800QILIUR11PB4H30"),
        resolved_id="cp-2",
        decision="match",
        score=0.91,
        evidence=[],
        approver_id="ada.l",
    )

    with pytest.raises(ledger.UnmergeRefusedError, match="since been merged"):
        ledger.unmerge(
            db,
            idempotency_key="u-1",
            reverses_entry_id=int(first["entry_id"]),
            approver_id="ada.l",
            reason="wrong",
        )


def test_an_unmerge_cannot_be_applied_twice(db: sqlite3.Connection) -> None:
    entry = ledger.merge(db, **MERGE)
    ledger.unmerge(
        db,
        idempotency_key="u-1",
        reverses_entry_id=int(entry["entry_id"]),
        approver_id="ada.l",
        reason="wrong",
    )

    with pytest.raises(ledger.UnmergeRefusedError, match="already reversed"):
        ledger.unmerge(
            db,
            idempotency_key="u-2",
            reverses_entry_id=int(entry["entry_id"]),
            approver_id="ada.l",
            reason="wrong again",
        )


# ------------------------------------------------------------------------------- the crosswalk


def test_both_legacy_shapes_read_as_one_record_type(db: sqlite3.Connection) -> None:
    """Ten numbered columns and a typed array arrive at the resolver identically."""
    records = {r.key: r for r in crosswalk.records_from(db)}

    assert set(records) == {
        "gleif:5493001KJTIIGC8Y1R12",
        "gleif:213800QILIUR11PB4H30",
        "companies_house:01234567",
    }
    assert records["companies_house:01234567"].other_names == (
        "BLOOMBERG FINANCE L.P.",
        "BLOOMBERG LP UK",
    )
    assert records["gleif:5493001KJTIIGC8Y1R12"].other_names == ("Bloomberg Finance Limited",)


def test_the_crosswalk_joins_the_two_systems_on_the_key_that_exists_in_the_data(
    db: sqlite3.Connection,
) -> None:
    """RA000585 plus `registered_as` is the only link between GLEIF and Companies House."""
    rows = db.execute("SELECT lei, company_number FROM crosswalk").fetchall()

    assert rows == [("5493001KJTIIGC8Y1R12", "01234567")]
