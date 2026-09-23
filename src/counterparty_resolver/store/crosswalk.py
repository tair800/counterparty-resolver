"""Reading two incompatible legacy schemas as one shape, without touching either.

The crosswalk is a **view**, and that is the whole brownfield point: the resolver needs GLEIF
records and Companies House records to look alike, and the way to get that is to write a view
rather than add a column to somebody else's table.

Two mismatches it has to absorb, both real:

- **Prior names.** GLEIF publishes a typed, unbounded array; Companies House publishes ten numbered
  columns because its CSV header says `Previous Names (occurs max 10)`. The view unpivots the ten
  into rows so both sides answer the same question. A record with eleven previous names loses the
  eleventh, and it loses it in the *source file*, not here.
- **The join key.** GLEIF stores the Companies House number in `registered_as` when
  `registered_at = 'RA000585'`. That is the only link between the two systems and it is not a
  foreign key anybody declared — 111,717 LEI records happen to carry it, so the crosswalk is a
  convention discovered in the data rather than a contract.

`connect` builds a database in this shape from nothing, which is what the tests and the demo both
use. There is no migration path from a real Companies House extract in this repository, and the
README says so rather than implying one.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from counterparty_resolver.domain import CounterpartyRecord
from counterparty_resolver.store.migrations import migrate, run
from counterparty_resolver.store.schema import (
    COMPANIES_HOUSE_PREVIOUS_NAME_COLUMNS,
    LEGACY_SCHEMA,
)

__all__ = ["COMPANIES_HOUSE_AUTHORITY", "CROSSWALK_VIEWS", "connect", "records_from", "seed"]

#: The GLEIF registration authority whose `registered_as` is a Companies House company number.
COMPANIES_HOUSE_AUTHORITY = "RA000585"

# The SQL below is built by interpolation, and `pyproject.toml` silences S608 for this file with
# the reason. Everything interpolated is a module constant declared in `schema.py` -- a column
# count and a registration-authority code -- and nothing here is ever reached by a request. The
# alternative is ten hand-written UNION branches that drift from the schema the first time the
# source file changes shape.
_UNPIVOT = "\nUNION ALL\n".join(
    f"""    SELECT company_number AS source_id, previous_name_{n} AS name, {n} AS ordinal
      FROM legacy_companies_house WHERE previous_name_{n} IS NOT NULL"""
    for n in range(1, COMPANIES_HOUSE_PREVIOUS_NAME_COLUMNS + 1)
)

#: Additive by construction: views read the legacy tables and cannot alter them.
#: Additive by construction: views read the legacy tables and cannot alter them.
#:
#: A tuple, like every other DDL in this package, so `migrations.run` can execute the three
#: statements inside one transaction. As a single script a half-applied failure left the tables
#: created and the views missing, and `connect` then skipped creation forever after.
CROSSWALK_VIEWS: tuple[str, ...] = (
    f"""CREATE VIEW counterparty_record AS
    SELECT 'gleif'           AS source,
           lei               AS source_id,
           legal_name        AS legal_name,
           country           AS country,
           city              AS city,
           postal_code       AS postal_code,
           address_line      AS address_line,
           registered_at     AS registration_authority,
           registered_as     AS registered_as
      FROM legacy_gleif
    UNION ALL
    SELECT 'companies_house' AS source,
           company_number    AS source_id,
           company_name      AS legal_name,
           country_of_origin AS country,
           post_town         AS city,
           post_code         AS postal_code,
           address_line_1    AS address_line,
           '{COMPANIES_HOUSE_AUTHORITY}' AS registration_authority,
           company_number    AS registered_as
      FROM legacy_companies_house""",
    f"""CREATE VIEW counterparty_other_name AS
    SELECT 'gleif' AS source, lei AS source_id, name AS name, 0 AS ordinal
      FROM legacy_gleif_other_name
    UNION ALL
    SELECT 'companies_house' AS source, source_id, name, ordinal FROM (
{_UNPIVOT}
    )""",
    f"""CREATE VIEW crosswalk AS
    SELECT g.lei                AS lei,
           c.company_number     AS company_number,
           g.legal_name         AS gleif_name,
           c.company_name       AS companies_house_name
      FROM legacy_gleif g
      JOIN legacy_companies_house c
        ON g.registered_at = '{COMPANIES_HOUSE_AUTHORITY}'
       AND g.registered_as = c.company_number""",
)


def connect(
    path: Path | str = ":memory:",
    *,
    target: int | None = None,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """A database with both legacy schemas, the crosswalk views, and the resolution layer applied.

    `target` stops the migration part-way, which is how the expand/contract tests hold the
    database in its intermediate states and run the application against each one.

    `check_same_thread=False` is for the console: Starlette runs sync endpoints on a thread
    pool and an in-memory database cannot be reopened per request, because it *is* the state.
    The caller that passes it owns the serialisation, and `api.Console` does it with one lock.
    """
    connection = sqlite3.connect(path, check_same_thread=check_same_thread)
    connection.execute("PRAGMA foreign_keys = ON")
    fresh = (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='legacy_gleif'"
        ).fetchone()
        is None
    )
    if fresh:
        # One transaction over both, because a half-created schema is the worst of the three
        # outcomes: `connect` would skip creation on every later call and the views would never
        # exist, with nothing to say why.
        run(connection, (*LEGACY_SCHEMA, *CROSSWALK_VIEWS))
    migrate(connection, target=target)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def seed(
    connection: sqlite3.Connection,
    *,
    gleif: list[dict[str, Any]],
    companies_house: list[dict[str, Any]],
) -> None:
    """Load rows into the legacy tables as their own source systems would have.

    Deliberately positional about nothing: each dict carries exactly the columns its own schema has,
    because writing a helper that "normalises" the two on the way in would hide the mismatch this
    project exists to demonstrate.
    """
    with connection:
        for row in gleif:
            other = row.pop("other_names", [])
            columns = ", ".join(row)
            connection.execute(
                f"INSERT INTO legacy_gleif ({columns}) VALUES ({', '.join('?' * len(row))})",
                tuple(row.values()),
            )
            for name_type, name in other:
                connection.execute(
                    "INSERT INTO legacy_gleif_other_name (lei, name_type, name) VALUES (?, ?, ?)",
                    (row["lei"], name_type, name),
                )
        for row in companies_house:
            columns = ", ".join(row)
            connection.execute(
                f"INSERT INTO legacy_companies_house ({columns}) "
                f"VALUES ({', '.join('?' * len(row))})",
                tuple(row.values()),
            )


def records_from(connection: sqlite3.Connection) -> list[CounterpartyRecord]:
    """Every source record, in the one shape the resolver understands.

    The resolver never learns which legacy schema a record came from beyond its `source`, which is
    the test of whether the crosswalk did its job.
    """
    connection.row_factory = sqlite3.Row
    names: dict[tuple[str, str], list[str]] = {}
    for row in connection.execute(
        "SELECT source, source_id, name FROM counterparty_other_name "
        "ORDER BY source, source_id, ordinal"
    ):
        names.setdefault((row["source"], row["source_id"]), []).append(row["name"])

    records = []
    for row in connection.execute("SELECT * FROM counterparty_record ORDER BY source, source_id"):
        key = (row["source"], row["source_id"])
        records.append(
            CounterpartyRecord(
                source=row["source"],
                source_id=row["source_id"],
                legal_name=row["legal_name"],
                other_names=tuple(names.get(key, ())),
                country=row["country"],
                city=row["city"],
                postal_code=row["postal_code"],
                address_lines=(row["address_line"],) if row["address_line"] else (),
                registration_authority=row["registration_authority"],
                registered_as=row["registered_as"],
            )
        )
    return records
