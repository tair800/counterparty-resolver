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

The check-then-insert is a race on its own — two callers can both find nothing and both insert — so
the UNIQUE constraint is the authority and the `IntegrityError` it raises is handled rather than
propagated: the loser re-reads and returns the winner's entry. That makes the guarantee a property
of this module instead of a property of whatever lock the caller happens to hold.

**Reversible, including source-system linkage.** `unmerge` restores `source_link` exactly as it was,
which is the part usually missed: dropping the resolved entity is easy, and putting each source
record back under the identifier it had before the merge is what the downstream systems actually
join on. `test_store.py` asserts the restored state is byte-identical to a snapshot taken before the
merge, rather than asserting that unmerge ran.

*"Exactly as it was"* includes the case where it was not nothing. A merge that moves a record from
one resolved entity to another **displaces** a link, and the first version of this module had
nowhere to put the displaced one: reversing that merge deleted the row and left the record under no
entity at all. Merge A with B, merge A with C, reverse the second, and A ended up unlinked instead
of back with B. The displaced links are now stored on the entry that displaced them and put back by
the entry that reverses it — which is the only way the sentence above can be true of a chain rather
than only of a clean slate.

**It writes whatever shape of the table it finds.** That is what the middle of an
expand/contract rename actually requires, and it was missing: the writer named a fixed set of
columns, so at migration 2 and 3 — precisely the states the sequence exists to make safe —
every merge failed on `NOT NULL constraint failed: merge_ledger.approved_by`. The window the
rename removes was the only window in which the shipped writer could not write. `_columns`
reads the live table and `merge` fills `approved_by` alongside `approver_id` while it is
there, which is the "transition" phase `migrations.py` describes.

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


def _columns(connection: sqlite3.Connection) -> frozenset[str]:
    """The columns `merge_ledger` actually has right now.

    Read per call rather than cached: a long-lived process is exactly the thing that is still
    running when a migration lands, and a cache would make it write the shape the table had at
    boot.
    """
    return frozenset(str(row[1]) for row in connection.execute("PRAGMA table_info(merge_ledger)"))


def _insert(connection: sqlite3.Connection, values: dict[str, Any]) -> int:
    """Insert whichever of `values` the table can hold, and return the new entry id.

    Columns the table does not have yet are dropped; columns it still has and this code no longer
    cares about are filled from their replacement. Both directions are needed, because during a
    rolling deploy the code and the schema disagree in both directions at once.
    """
    present = _columns(connection)
    if "approved_by" in present:
        # The transition phase: the old column is still NOT NULL and the old readers still read it.
        values = {**values, "approved_by": values["approver_id"]}
    usable = {name: value for name, value in values.items() if name in present}
    placeholders = ", ".join("?" * len(usable))
    cursor = connection.execute(
        f"INSERT INTO merge_ledger ({', '.join(usable)}) "  # noqa: S608 - keys are schema columns
        f"VALUES ({placeholders})",
        tuple(usable.values()),
    )
    return int(cursor.lastrowid or 0)


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

    # Read what these two records are linked to *before* anything is written, because the upsert
    # below is about to overwrite it and the reversal will need it back.
    displaced = _links_of(connection, (left, right))

    stamp = (now or dt.datetime.now(tz=dt.UTC)).isoformat(timespec="seconds")
    try:
        with connection:
            entry_id = _insert(
                connection,
                {
                    "idempotency_key": idempotency_key,
                    "action": "merge",
                    "left_source": left[0],
                    "left_id": left[1],
                    "right_source": right[0],
                    "right_id": right[1],
                    "resolved_id": resolved_id,
                    "decision": decision,
                    "score": score,
                    "evidence_json": json.dumps(evidence, sort_keys=True),
                    "approver_id": approver_id,
                    "recorded_at": stamp,
                    "displaced_links_json": json.dumps(displaced, sort_keys=True),
                },
            )
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
    except sqlite3.IntegrityError:
        # Lost the race on `idempotency_key`. The winner's entry is the answer, which is what a
        # retry asked for.
        won = _existing(connection, idempotency_key)
        if won is None:
            raise
        return won

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
        entry_id = _insert(
            connection,
            {
                "idempotency_key": idempotency_key,
                "action": "unmerge",
                "left_source": original["left_source"],
                "left_id": original["left_id"],
                "right_source": original["right_source"],
                "right_id": original["right_id"],
                "resolved_id": original["resolved_id"],
                "reverses_entry_id": reverses_entry_id,
                "decision": "no_match",
                "score": None,
                "evidence_json": json.dumps(
                    [{"feature": "unmerge_reason", "detail": reason}], sort_keys=True
                ),
                "approver_id": approver_id,
                "recorded_at": stamp,
            },
        )
        # Deleting the link row is what restores the prior state *when there was no prior row*:
        # leaving one behind with a null `resolved_id` would be a different state that merely looks
        # unmerged. Where the merge displaced an existing link, the row is put back instead --
        # deleting it there would silently unlink a record from an entity nobody reversed.
        for source, source_id in sides:
            connection.execute(
                "DELETE FROM source_link WHERE source = ? AND source_id = ? "
                "AND linked_by_entry_id = ?",
                (source, source_id, reverses_entry_id),
            )
        # `.get`, because the column only exists from migration 5. A reversal of an entry
        # written before it simply has nothing to restore, which is the correct reading.
        for link in json.loads(dict(original).get("displaced_links_json") or "[]"):
            connection.execute(
                "INSERT INTO source_link (source, source_id, resolved_id, linked_by_entry_id) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(source, source_id) DO UPDATE SET "
                "resolved_id = excluded.resolved_id, "
                "linked_by_entry_id = excluded.linked_by_entry_id",
                (
                    link["source"],
                    link["source_id"],
                    link["resolved_id"],
                    link["linked_by_entry_id"],
                ),
            )

    return _row(connection, entry_id)


def _links_of(
    connection: sqlite3.Connection, sides: tuple[tuple[str, str], ...]
) -> list[dict[str, Any]]:
    """The `source_link` rows these records hold right now, as plain dicts.

    Stored on the ledger entry rather than recomputed at reversal time, because by then the
    information is gone -- that is the whole point of recording it.
    """
    out: list[dict[str, Any]] = []
    for source, source_id in sides:
        row = connection.execute(
            "SELECT resolved_id, linked_by_entry_id FROM source_link "
            "WHERE source = ? AND source_id = ?",
            (source, source_id),
        ).fetchone()
        if row is not None:
            out.append(
                {
                    "source": source,
                    "source_id": source_id,
                    "resolved_id": row[0],
                    "linked_by_entry_id": row[1],
                }
            )
    return out


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
