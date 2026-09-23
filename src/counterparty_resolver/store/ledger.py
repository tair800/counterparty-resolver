"""The merge ledger: append-only, idempotent, and reversible to the byte.

Three properties, each of which exists because of a specific way this goes wrong in production.

**Append-only.** A merge that turns out to be wrong is unpicked by *adding* a reversing entry, never
by editing or deleting the original. The database enforces it — `BEFORE UPDATE` and `BEFORE DELETE`
triggers on the table — so the property survives code that forgets about it. A corrected merge whose
history is gone is indistinguishable from a merge that never happened, and the person asking why a
payment went to the wrong counterparty needs the difference.

**Idempotent.** Every write carries an `idempotency_key` with a UNIQUE constraint. Replaying a merge
returns the original entry and changes nothing. This is not defensive coding; it is the only way a
retry after a timeout is safe, and the approval queue will retry.

**Reversible, including source-system linkage.** `unmerge` restores `source_link` exactly as it was,
which is the part usually missed: dropping the resolved entity is easy, and putting each source
record back under the identifier it had before the merge is what the downstream systems actually
join on. `test_store.py` asserts the restored state is byte-identical to a snapshot taken before the
merge, rather than asserting that unmerge ran.

**What this module does not do.** It does not decide. `resolve.py` decides, a person approves, and
this records. A merge arrives here with the evidence that justified it, which is stored verbatim so
the ledger answers "why" and not only "what".
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any

__all__ = [
    "LedgerEntry",
    "UnmergeRefusedError",
    "links_for",
    "merge",
    "resolved_id_for",
    "unmerge",
]


class UnmergeRefusedError(RuntimeError):
    """The reversal could not be performed, and doing it anyway would corrupt the linkage."""


class LedgerEntry(dict[str, Any]):
    """One row, as a plain mapping. The ledger's shape is the schema's, not a model's."""


def _row(connection: sqlite3.Connection, entry_id: int) -> LedgerEntry:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT * FROM merge_ledger WHERE entry_id = ?", (entry_id,)
    ).fetchone()
    return LedgerEntry(dict(row))


def _existing(connection: sqlite3.Connection, idempotency_key: str) -> LedgerEntry | None:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT * FROM merge_ledger WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    return LedgerEntry(dict(row)) if row else None


def merge(
    connection: sqlite3.Connection,
    *,
    idempotency_key: str,
    left: tuple[str, str],
    right: tuple[str, str],
    resolved_id: str,
    decision: str,
    score: float | None,
    evidence: list[dict[str, Any]],
    approver_id: str,
    now: dt.datetime | None = None,
) -> LedgerEntry:
    """Record an approved merge and point both source records at `resolved_id`.

    Args:
        left: ``(source, source_id)`` of one side, e.g. ``("gleif", "5493...")``.
        right: The other side.
        resolved_id: The identifier the merged entity is known by downstream.
        evidence: The feature contributions the decision was made on, stored verbatim.

    Returns:
        The ledger entry. On a replayed `idempotency_key`, the **original** entry, unchanged.
    """
    replay = _existing(connection, idempotency_key)
    if replay is not None:
        return replay

    stamp = (now or dt.datetime.now(tz=dt.UTC)).isoformat(timespec="seconds")
    with connection:
        cursor = connection.execute(
            "INSERT INTO merge_ledger (idempotency_key, action, left_source, left_id, "
            "right_source, right_id, resolved_id, decision, score, evidence_json, "
            "approver_id, recorded_at) VALUES (?, 'merge', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                idempotency_key,
                *left,
                *right,
                resolved_id,
                decision,
                score,
                json.dumps(evidence, sort_keys=True),
                approver_id,
                stamp,
            ),
        )
        entry_id = int(cursor.lastrowid or 0)
        # `INSERT OR REPLACE` would silently discard which entry first linked a record, and that
        # column is how `unmerge` knows what it is allowed to undo.
        for source, source_id in (left, right):
            connection.execute(
                "INSERT INTO source_link (source, source_id, resolved_id, linked_by_entry_id) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(source, source_id) DO UPDATE SET "
                "resolved_id = excluded.resolved_id, "
                "linked_by_entry_id = excluded.linked_by_entry_id",
                (source, source_id, resolved_id, entry_id),
            )

    return _row(connection, entry_id)


def unmerge(
    connection: sqlite3.Connection,
    *,
    idempotency_key: str,
    reverses_entry_id: int,
    approver_id: str,
    reason: str,
    now: dt.datetime | None = None,
) -> LedgerEntry:
    """Reverse one merge by appending an entry, and restore the linkage it created.

    Refuses when a later merge has moved either source record on. Undoing the middle of a chain
    would leave the newer merge pointing at a resolved entity nothing links to any more, and
    silently repairing that would be this function inventing a decision nobody approved.
    """
    connection.row_factory = sqlite3.Row
    replay = _existing(connection, idempotency_key)
    if replay is not None:
        return replay

    original = connection.execute(
        "SELECT * FROM merge_ledger WHERE entry_id = ? AND action = 'merge'",
        (reverses_entry_id,),
    ).fetchone()
    if original is None:
        raise UnmergeRefusedError(f"entry {reverses_entry_id} is not a merge in this ledger")

    already = connection.execute(
        "SELECT entry_id FROM merge_ledger WHERE action = 'unmerge' AND reverses_entry_id = ?",
        (reverses_entry_id,),
    ).fetchone()
    if already is not None:
        raise UnmergeRefusedError(f"entry {reverses_entry_id} was already reversed by {already[0]}")

    sides = (
        (original["left_source"], original["left_id"]),
        (original["right_source"], original["right_id"]),
    )
    for source, source_id in sides:
        link = connection.execute(
            "SELECT linked_by_entry_id FROM source_link WHERE source = ? AND source_id = ?",
            (source, source_id),
        ).fetchone()
        if link is not None and link[0] != reverses_entry_id:
            raise UnmergeRefusedError(
                f"{source}:{source_id} has since been merged by entry {link[0]}; "
                "reverse that one first"
            )

    stamp = (now or dt.datetime.now(tz=dt.UTC)).isoformat(timespec="seconds")
    with connection:
        cursor = connection.execute(
            "INSERT INTO merge_ledger (idempotency_key, action, left_source, left_id, "
            "right_source, right_id, resolved_id, reverses_entry_id, decision, score, "
            "evidence_json, approver_id, recorded_at) "
            "VALUES (?, 'unmerge', ?, ?, ?, ?, ?, ?, 'no_match', NULL, ?, ?, ?)",
            (
                idempotency_key,
                original["left_source"],
                original["left_id"],
                original["right_source"],
                original["right_id"],
                original["resolved_id"],
                reverses_entry_id,
                json.dumps([{"feature": "unmerge_reason", "detail": reason}], sort_keys=True),
                approver_id,
                stamp,
            ),
        )
        # Deleting the link row is what restores the prior state exactly: before the merge there was
        # no row, so leaving one behind with a null `resolved_id` would be a different state that
        # merely looks unmerged.
        for source, source_id in sides:
            connection.execute(
                "DELETE FROM source_link WHERE source = ? AND source_id = ? "
                "AND linked_by_entry_id = ?",
                (source, source_id, reverses_entry_id),
            )

    return _row(connection, int(cursor.lastrowid or 0))


def resolved_id_for(connection: sqlite3.Connection, source: str, source_id: str) -> str | None:
    """What one source record currently resolves to, or `None` if it stands alone."""
    row = connection.execute(
        "SELECT resolved_id FROM source_link WHERE source = ? AND source_id = ?",
        (source, source_id),
    ).fetchone()
    return str(row[0]) if row else None


def links_for(connection: sqlite3.Connection, resolved_id: str) -> list[tuple[str, str]]:
    """Every source record currently under one resolved entity, in a stable order."""
    rows = connection.execute(
        "SELECT source, source_id FROM source_link WHERE resolved_id = ? "
        "ORDER BY source, source_id",
        (resolved_id,),
    ).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]
