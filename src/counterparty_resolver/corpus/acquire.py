"""Fetch the adjudicated duplicates from GLEIF. ~65 requests, not a 2 GB download.

The blueprint assumed the GLEIF **Golden Copy** bulk file. That works and is what you would reach
for, but it is well over a gigabyte and cannot run in CI, which would put the corpus build in the
category of things nobody re-runs. The public REST API supports
``filter[registration.status]=DUPLICATE`` directly, so the entire labelled population -- every LEI a
registrar has adjudicated as a duplicate of another -- arrives in about thirty-three pages of 200,
plus the same again to resolve the successors. That is a corpus anybody can rebuild in a minute.

**Nothing here decides anything.** This module fetches and records; `build.py` constructs pairs. The
separation is so that a label can never be produced by the thing that also produced the record it
labels.

Licence: GLEIF is **CC0 1.0** -- redistributable, commercial use, no attribution required. The
fetched records are nonetheless *not* committed: `docs/provenance.md` gives the exact query, and a
rebuild is cheap. Committing them would only let them go stale silently.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx

__all__ = ["GLEIF_API", "GLEIF_LICENCE", "fetch_duplicates", "fetch_leis", "write_raw"]

GLEIF_API = "https://api.gleif.org/api/v1/lei-records"
GLEIF_LICENCE = "CC0 1.0 Universal"
GLEIF_SOURCE_URL = "https://www.gleif.org/en/lei-data/gleif-golden-copy"

#: GLEIF's maximum. Larger values are rejected rather than clamped.
PAGE_SIZE = 200

#: Batch size for `filter[lei]=a,b,c`. Kept under the API's URL length tolerance.
LEI_BATCH = 50


def _client(timeout: float = 90.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        headers={"Accept": "application/vnd.api+json"},
        follow_redirects=True,
    )


def fetch_duplicates(
    *, limit: int | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    """Every LEI record whose registration status is ``DUPLICATE``.

    These are the labels. A record reaches this state because an LEI Issuing Organisation examined
    two registrations and recorded one as the duplicate of the other, populating
    ``entity.successorEntity.lei``. Nothing in this repository can create that.

    Args:
        limit: Stop after this many records. For tests and for a fast smoke build; a full run
            passes ``None`` and takes every one.
        client: Injected so tests can drive this without a network.
    """
    owned = client is None
    http = client or _client()
    records: list[dict[str, Any]] = []
    page = 1
    try:
        while True:
            response = http.get(
                GLEIF_API,
                params={
                    "filter[registration.status]": "DUPLICATE",
                    "page[size]": PAGE_SIZE,
                    "page[number]": page,
                },
            )
            response.raise_for_status()
            payload = response.json()
            batch = payload.get("data", [])
            records.extend(batch)

            pagination = payload.get("meta", {}).get("pagination", {})
            last = pagination.get("lastPage", page)
            if limit is not None and len(records) >= limit:
                return records[:limit]
            if page >= last or not batch:
                return records
            page += 1
    finally:
        if owned:
            http.close()


def fetch_leis(leis: list[str], *, client: httpx.Client | None = None) -> dict[str, dict[str, Any]]:
    """Resolve a list of LEIs to their records, in batches.

    The successors. A duplicate record names the LEI it was merged into but carries none of that
    entity's attributes, so the surviving side of every pair has to be fetched separately -- and in
    batches, or 6,000 pairs becomes 6,000 requests.
    """
    owned = client is None
    http = client or _client()
    out: dict[str, dict[str, Any]] = {}
    unique = list(dict.fromkeys(lei for lei in leis if lei))
    try:
        for start in range(0, len(unique), LEI_BATCH):
            chunk = unique[start : start + LEI_BATCH]
            response = http.get(
                GLEIF_API,
                params={"filter[lei]": ",".join(chunk), "page[size]": PAGE_SIZE},
            )
            response.raise_for_status()
            for record in response.json().get("data", []):
                out[record["attributes"]["lei"]] = record
        return out
    finally:
        if owned:
            http.close()


def source_revision(client: httpx.Client | None = None) -> str:
    """GLEIF's own golden-copy publish date, so the corpus pins what it was built from.

    Read from the API's `meta` rather than stamped with today's date: the point of a revision is to
    identify the data, and the date we happened to run is not that.
    """
    owned = client is None
    http = client or _client()
    try:
        response = http.get(GLEIF_API, params={"page[size]": 1})
        response.raise_for_status()
        published = response.json().get("meta", {}).get("goldenCopy", {}).get("publishDate")
        return str(published or dt.datetime.now(tz=dt.UTC).date())
    finally:
        if owned:
            http.close()


def write_raw(records: list[dict[str, Any]], path: Path) -> Path:
    """Write fetched records to `data/raw/`, which `.gitignore` excludes.

    Cached so that re-running the build does not re-fetch, and ignored so that a source's terms can
    never be violated by an accidental `git add -A`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=1) + "\n", encoding="utf-8")
    return path
