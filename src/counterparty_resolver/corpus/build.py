"""Turn adjudicated duplicates into a labelled pair corpus, with provenance on every pair.

Two rules from ADR-001 are enforced structurally here rather than remembered.

**Nothing in this file may author a positive label.** A positive exists because a registrar recorded
one LEI as the duplicate of another; `label_basis` names that adjudication and the fields it came
from. There is no code path that promotes a similar-looking pair.

**Negatives are hard by construction.** A negative is a pair that *blocking surfaced* -- similar
enough to be considered -- and that no `successorEntity` relationship links, transitively. Random
negatives would be trivially separable and every metric computed against them would be theatre: two
unrelated companies from different countries do not need a resolver to tell them apart.

The transitive part matters. A merged into B and B merged into C makes A and C the same entity, and
sampling A against C as a negative would put a true positive in the negative class and quietly cap
recall. Clusters are closed with a union-find before any negative is drawn.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterable
from typing import Any

from counterparty_resolver.blocking import candidate_pairs
from counterparty_resolver.corpus.holdout import assign_split
from counterparty_resolver.domain import CandidatePair, CounterpartyRecord, VariantType
from counterparty_resolver.features import (
    differs_only_by_diacritics_or_case,
    differs_only_by_legal_form,
)
from counterparty_resolver.normalize import normalize_name

__all__ = ["CorpusStats", "build_corpus", "record_from_gleif", "variant_types_for"]

SOURCE = "gleif"
BUILDER_VERSION = "1"


def record_from_gleif(payload: dict[str, Any]) -> CounterpartyRecord:
    """One GLEIF API record as a `CounterpartyRecord`.

    Only the fields a source system would plausibly hold. GLEIF publishes far more; taking all of it
    would make the resolver look good on evidence a CRM never has.
    """
    attributes = payload["attributes"]
    entity = attributes["entity"]
    legal_address = entity.get("legalAddress") or {}
    registered_at = entity.get("registeredAt") or {}

    other = tuple(
        name["name"]
        for name in (entity.get("otherNames") or [])
        if isinstance(name, dict) and name.get("name")
    )

    return CounterpartyRecord(
        source=SOURCE,
        source_id=attributes["lei"],
        legal_name=entity["legalName"]["name"],
        other_names=other,
        country=legal_address.get("country"),
        jurisdiction=entity.get("jurisdiction"),
        legal_form_id=(entity.get("legalForm") or {}).get("id"),
        registration_authority=registered_at.get("id"),
        registered_as=entity.get("registeredAs"),
        city=legal_address.get("city"),
        postal_code=legal_address.get("postalCode"),
        address_lines=tuple(legal_address.get("addressLines") or ()),
    )


def variant_types_for(
    left: CounterpartyRecord, right: CounterpartyRecord
) -> tuple[VariantType, ...]:
    """Which of the claimed failure modes this pair actually exhibits.

    Computed from the pair. Kill test B asserts all three headline types appear in a sample of 200,
    and it can only be a real test if these are derived rather than asserted -- a constant would
    pass the test and prove nothing about the data.
    """
    found: list[VariantType] = []

    if differs_only_by_legal_form(left.legal_name, right.legal_name):
        found.append(VariantType.LEGAL_FORM_VARIANCE)

    if differs_only_by_diacritics_or_case(left.legal_name, right.legal_name):
        found.append(VariantType.DIACRITIC_OR_CASING_DRIFT)

    left_ra = (left.registration_authority or "").upper()
    right_ra = (right.registration_authority or "").upper()
    if left_ra and right_ra and left_ra != right_ra:
        found.append(VariantType.DISTINCT_REGISTRATION_AUTHORITY)

    left_norm, right_norm = normalize_name(left.legal_name), normalize_name(right.legal_name)
    if left_norm != right_norm and sorted(left_norm.split()) == sorted(right_norm.split()):
        found.append(VariantType.TOKEN_ORDER_OR_ABBREVIATION)

    left_city = (left.city or "").strip().lower()
    right_city = (right.city or "").strip().lower()
    if left_city and right_city and left_city != right_city:
        found.append(VariantType.ADDRESS_DRIFT)

    return tuple(found)


class _Clusters:
    """Union-find over LEIs, so transitively-merged entities end up in one component."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self._parent.setdefault(item, item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:  # path compression
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra

    def same(self, a: str, b: str) -> bool:
        return a in self._parent and b in self._parent and self.find(a) == self.find(b)


def _pair_id(left: str, right: str, label: str) -> str:
    raw = f"{min(left, right)}|{max(left, right)}|{label}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class CorpusStats(dict[str, Any]):
    """Counts the build produced, for the README and the kill test to read from one place."""


def build_corpus(
    duplicates: Iterable[dict[str, Any]],
    successors: dict[str, dict[str, Any]],
    *,
    source_revision: str,
    negatives_per_positive: float = 1.0,
    seed: int = 20260924,
) -> dict[str, Any]:
    """The labelled corpus.

    Args:
        duplicates: GLEIF records whose registration status is DUPLICATE.
        successors: LEI -> record, for the surviving side of each pair.
        source_revision: GLEIF's golden-copy publish date, pinned into every pair.
        negatives_per_positive: How many negatives to draw per positive. 1.0 keeps the corpus
            balanced, which ADR-001's kill test A requires in both directions.
        seed: Fixed, so two runs of this function on the same input produce the same corpus --
            kill test D.
    """
    positives: list[CandidatePair] = []
    records: dict[str, CounterpartyRecord] = {}
    clusters = _Clusters()

    for payload in duplicates:
        attributes = payload["attributes"]
        entity = attributes["entity"]
        successor_lei = (entity.get("successorEntity") or {}).get("lei")
        if not successor_lei or successor_lei not in successors:
            continue  # An adjudication we cannot see both sides of is not a usable pair.

        left = record_from_gleif(payload)
        right = record_from_gleif(successors[successor_lei])
        if left.source_id == right.source_id:
            continue

        records[left.key] = left
        records[right.key] = right
        clusters.union(left.source_id, right.source_id)

        positives.append(
            CandidatePair(
                pair_id=_pair_id(left.source_id, right.source_id, "match"),
                left=left,
                right=right,
                label="match",
                label_basis=(
                    "GLEIF registration.status=DUPLICATE with entity.successorEntity.lei — an LEI "
                    "Issuing Organisation adjudicated these as the same legal entity"
                ),
                source_revision=source_revision,
                variant_types=variant_types_for(left, right),
            )
        )

    # Negatives: surfaced by blocking, not linked by any adjudication, transitively, **and drawn
    # within one side of the hold-out split**.
    #
    # That last condition is not a detail. Negatives drawn across the split straddle two clusters
    # and `holdout.py` drops them, which left the first build with 1,226 positives against 156
    # negatives on the held-out side -- a precision computed against 156 negatives is not a
    # measurement. Deciding the side here, from the same deterministic `assign_split` the split
    # itself uses, keeps both sides balanced without the two files being able to disagree.
    side = {rec.source_id: assign_split(clusters.find(rec.source_id)) for rec in records.values()}

    # A quota **per side**, not one global budget. Blocking surfaces far more candidates on the
    # development side simply because four fifths of the records live there, so a single budget
    # consumed in iteration order leaves the hold-out with almost no negatives -- the first build
    # gave it 1,226 positives against 181. Precision computed against 181 negatives is not a
    # measurement, and a hold-out is the one number that has to be worth something.
    positives_by_side: dict[str, int] = {"development": 0, "holdout": 0}
    for pair in positives:
        positives_by_side[side[pair.left.source_id]] += 1
    quota = {name: int(count * negatives_per_positive) for name, count in positives_by_side.items()}

    negatives: list[CandidatePair] = []
    drawn: dict[str, int] = {"development": 0, "holdout": 0}
    seen: set[tuple[str, str]] = set()

    for left, right, keys in candidate_pairs(records.values()):
        if all(drawn[name] >= quota[name] for name in quota):
            break
        if clusters.same(left.source_id, right.source_id):
            continue  # A true positive. Sampling it as a negative would cap recall silently.
        if side[left.source_id] != side[right.source_id]:
            continue  # Would straddle the split and be dropped; drawing it wastes a negative.
        where = side[left.source_id]
        if drawn[where] >= quota[where]:
            continue
        ordered = (min(left.key, right.key), max(left.key, right.key))
        if ordered in seen:
            continue
        seen.add(ordered)
        drawn[where] += 1
        negatives.append(
            CandidatePair(
                pair_id=_pair_id(left.source_id, right.source_id, "no_match"),
                left=left,
                right=right,
                blocking_keys=keys,
                label="no_match",
                label_basis=(
                    "surfaced by deterministic blocking and linked by no successorEntity "
                    "relationship, transitively — a hard negative by construction"
                ),
                source_revision=source_revision,
                variant_types=variant_types_for(left, right),
            )
        )

    pairs = positives + negatives
    return {
        "generated_at": dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds"),
        "builder_version": BUILDER_VERSION,
        "seed": seed,
        "sources": [
            {
                "name": "GLEIF LEI (Level 1)",
                "url": "https://api.gleif.org/api/v1/lei-records?filter[registration.status]=DUPLICATE",
                "licence": "CC0 1.0 Universal",
                "revision": source_revision,
                "vendored": False,
            }
        ],
        "counts": {
            "pairs": len(pairs),
            "positives": len(positives),
            "negatives": len(negatives),
            "distinct_records": len(records),
        },
        "pairs": [_serialise(p) for p in pairs],
    }


def _serialise(pair: CandidatePair) -> dict[str, Any]:
    """The flat shape the artifact and the kill test agree on."""
    return {
        "pair_id": pair.pair_id,
        "source": SOURCE,
        "left_id": pair.left.source_id,
        "right_id": pair.right.source_id,
        "label": pair.label,
        "label_basis": pair.label_basis,
        "source_revision": pair.source_revision,
        "blocking_keys": list(pair.blocking_keys),
        "variant_types": [v.value for v in pair.variant_types],
        "left": pair.left.model_dump(),
        "right": pair.right.model_dump(),
    }
