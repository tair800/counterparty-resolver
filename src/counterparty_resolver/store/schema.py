"""The two legacy schemas, and the rule that this project may not change them.

`PORTFOLIO_BLUEPRINT.md` fixes the brownfield constraint for this project: *"seeded source schemas
are fixed and may not be altered, so the resolution layer is additive."* That is the constraint a
mid-level hire actually works under, and the only way to demonstrate it honestly is to make it
enforceable rather than promised — so the legacy tables are declared here, fingerprinted, and
`test_store.py` fails the build if a migration touches one.

**The two schemas are genuinely different, and neither was designed for this.**

`legacy_gleif` keeps prior names the way GLEIF publishes them: a typed, unbounded array, flattened
here into a child table because that is what a relational source system does with one.
`legacy_companies_house` keeps them the way Companies House publishes them: **ten numbered
columns**, `previous_name_1` through `previous_name_10`, because the file format is a CSV whose
header reads `Previous Names (occurs max 10)`. One models a list; the other models a spreadsheet.
Resolving across them is the actual job, and no amount of wishing turns the second into the first.

The join between them is not invented either. 111,717 LEI records carry `registered_at = 'RA000585'`
with a real Companies House company number in `registered_as`, which is the crosswalk key
`crosswalk.py` uses.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

__all__ = [
    "LEGACY_FINGERPRINT",
    "LEGACY_SCHEMA",
    "LEGACY_TABLES",
    "RESOLUTION_SCHEMA_V1",
    "legacy_fingerprint",
]

#: Tables this project reads and must never alter. Named here so the guard has one list to read.
LEGACY_TABLES = ("legacy_gleif", "legacy_gleif_other_name", "legacy_companies_house")

#: How many numbered previous-name columns the Companies House extract has. Not a choice.
COMPANIES_HOUSE_PREVIOUS_NAME_COLUMNS = 10

_PREVIOUS_NAME_COLUMNS = ",\n    ".join(
    f"previous_name_{n} TEXT" for n in range(1, COMPANIES_HOUSE_PREVIOUS_NAME_COLUMNS + 1)
)

#: **Statements, not a script.** `executescript` issues a COMMIT before it runs and lets each
#: statement autocommit, so a script that fails half way leaves the database in the state it
#: reached. Every DDL in this package is therefore a tuple, executed one statement at a time
#: inside one explicit transaction -- see `migrations.transaction`.
LEGACY_SCHEMA: tuple[str, ...] = (
    """CREATE TABLE legacy_gleif (
    lei TEXT PRIMARY KEY,
    legal_name TEXT NOT NULL,
    legal_form TEXT,
    country TEXT,
    city TEXT,
    postal_code TEXT,
    address_line TEXT,
    registered_at TEXT,
    registered_as TEXT,
    registration_status TEXT,
    successor_lei TEXT
)""",
    """CREATE TABLE legacy_gleif_other_name (
    lei TEXT NOT NULL REFERENCES legacy_gleif(lei),
    name_type TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (lei, name_type, name)
)""",
    f"""CREATE TABLE legacy_companies_house (
    company_number TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    company_status TEXT,
    country_of_origin TEXT,
    post_town TEXT,
    post_code TEXT,
    address_line_1 TEXT,
    {_PREVIOUS_NAME_COLUMNS}
)""",
)

#: Version 1 of the layer this project owns. Everything in it is **additive**: it references the
#: legacy tables and never modifies them.
#:
#: `merge_ledger` is append-only by construction, not by convention -- there is no column to update
#: and the triggers below refuse UPDATE and DELETE outright. A merge and its reversal are two rows,
#: so the history of a decision survives the decision being wrong.
RESOLUTION_SCHEMA_V1: tuple[str, ...] = (
    """CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    phase TEXT NOT NULL,
    description TEXT NOT NULL
)""",
    """CREATE TABLE merge_ledger (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL CHECK (action IN ('merge', 'unmerge')),
    left_source TEXT NOT NULL,
    left_id TEXT NOT NULL,
    right_source TEXT NOT NULL,
    right_id TEXT NOT NULL,
    resolved_id TEXT NOT NULL,
    reverses_entry_id INTEGER REFERENCES merge_ledger(entry_id),
    decision TEXT NOT NULL,
    score REAL,
    evidence_json TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    recorded_at TEXT NOT NULL
)""",
    "CREATE INDEX merge_ledger_resolved ON merge_ledger(resolved_id)",
    "CREATE INDEX merge_ledger_left ON merge_ledger(left_source, left_id)",
    "CREATE INDEX merge_ledger_right ON merge_ledger(right_source, right_id)",
    """CREATE TRIGGER merge_ledger_is_append_only_update
BEFORE UPDATE ON merge_ledger
BEGIN
    SELECT RAISE(ABORT, 'merge_ledger is append-only: record a reversing entry instead');
END""",
    """CREATE TRIGGER merge_ledger_is_append_only_delete
BEFORE DELETE ON merge_ledger
BEGIN
    SELECT RAISE(ABORT, 'merge_ledger is append-only: record a reversing entry instead');
END""",
    """CREATE TABLE source_link (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    resolved_id TEXT NOT NULL,
    linked_by_entry_id INTEGER NOT NULL REFERENCES merge_ledger(entry_id),
    PRIMARY KEY (source, source_id)
)""",
    "CREATE INDEX source_link_resolved ON source_link(resolved_id)",
)


def legacy_fingerprint(statements: str | Iterable[str] = LEGACY_SCHEMA) -> str:
    """A stable digest of one DDL statement, or of several, whitespace-insensitive.

    The guard compares this against the schema SQLite actually reports, so a migration that
    alters a legacy table fails a test rather than being noticed later by whoever owns the
    source system.
    """
    text = statements if isinstance(statements, str) else "\n".join(statements)
    normalised = " ".join(text.split()).lower()
    return hashlib.sha256(normalised.encode()).hexdigest()[:32]


#: Computed once, at import, from the declaration above. The test asserts the *live database* still
#: matches this, so the check survives someone editing both the DDL and the constant.
LEGACY_FINGERPRINT = legacy_fingerprint()
