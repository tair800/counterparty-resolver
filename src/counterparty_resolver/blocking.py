"""Candidate generation. What this drops, nothing downstream can recover.

That sentence is why candidate recall is a published number with a floor in ADR-001 rather than an
implementation detail. A resolver reporting 0.99 precision over a candidate set that lost a third of
the true pairs has resolved a third less than it appears to.

Deterministic keys, no embeddings. The blueprint specifies pgvector and embedding-based candidate
generation and ADR-001 records that it is not built here — so the honest move is not to claim
blocking is as good, but to **measure what it costs** and publish it. Every key below earns its
place by recovering pairs the others miss; `scripts/evaluate.py` reports per-key contribution so a
key that recovers nothing can be deleted rather than kept for comfort.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator

from counterparty_resolver.domain import CounterpartyRecord
from counterparty_resolver.normalize import acronym, normalize_identifier, normalize_name, tokens

__all__ = ["BLOCKING_KEYS", "blocking_keys", "candidate_pairs", "index_by_key"]

#: A token has to be at least this long to anchor a block on its own. Shorter ones are articles and
#: initials, and blocking on them produces a block the size of the corpus.
_MIN_ANCHOR = 4

#: How many records one key may hold before it is treated as useless and skipped. A key matching
#: thousands of records is not narrowing anything; it is a full scan wearing a key's name.
MAX_BLOCK_SIZE = 200

#: An acronym of one letter matches half the corpus; two is the shortest that says anything.
_MIN_ACRONYM = 2

#: Registration numbers and postcodes shorter than this are too collision-prone to block on.
_MIN_IDENTIFIER = 4

#: A block of one record pairs with nothing.
_MIN_BLOCK = 2


def _sorted_name_prefix(normalized: str) -> str | None:
    """First four characters of the alphabetically-first long token.

    Sorting first is what makes this survive token reordering: `Alpha Beta Holdings` and `Beta Alpha
    Holdings` produce the same key. Prefixing is what makes it survive a suffix change.
    """
    candidates = sorted(t for t in normalized.split() if len(t) >= _MIN_ANCHOR)
    return candidates[0][:4] if candidates else None


def blocking_keys(record: CounterpartyRecord) -> tuple[str, ...]:
    """Every key this record should be findable under.

    Multiple keys per record on purpose. One key is one hypothesis about which part of a record
    survives corruption; a name key fails when the name was retyped, an identifier key fails when
    the identifier is absent, and the union is what gets recall above the floor.
    """
    keys: list[str] = []
    name = normalize_name(record.legal_name)

    if prefix := _sorted_name_prefix(name):
        keys.append(f"nameprefix:{prefix}")

    initials = acronym(name)
    if initials and len(initials) >= _MIN_ACRONYM:
        keys.append(f"acronym:{initials}")

    # The identifier alone, without its authority. Two registries can issue the same number to
    # different companies, so this is a *candidate* key and never a decision -- the feature layer
    # is where the authority is compared.
    identifier = normalize_identifier(record.registered_as)
    if identifier and len(identifier) >= _MIN_IDENTIFIER:
        keys.append(f"regid:{identifier}")

    # Longest token plus country. Survives both reordering and legal-form drift, and the country
    # keeps it from matching every company on earth that contains the word "holdings".
    longest = max((t for t in name.split() if len(t) >= _MIN_ANCHOR), key=len, default=None)
    if longest and record.country:
        keys.append(f"token:{longest}:{record.country.upper()}")

    postal = normalize_identifier(record.postal_code) if record.postal_code else None
    if postal and len(postal) >= _MIN_IDENTIFIER:
        keys.append(f"postal:{postal}")

    return tuple(dict.fromkeys(keys))


#: The key families, for reporting which of them actually recovers pairs.
BLOCKING_KEYS = ("nameprefix", "acronym", "regid", "token", "postal")


def index_by_key(records: Iterable[CounterpartyRecord]) -> dict[str, list[CounterpartyRecord]]:
    """Group records by every key each one produces."""
    index: dict[str, list[CounterpartyRecord]] = defaultdict(list)
    for record in records:
        for key in blocking_keys(record):
            index[key].append(record)
    return dict(index)


def candidate_pairs(
    records: Iterable[CounterpartyRecord],
) -> Iterator[tuple[CounterpartyRecord, CounterpartyRecord, tuple[str, ...]]]:
    """Every pair sharing at least one usable key, once, with the keys that surfaced it.

    Deduplicated on the unordered pair of record keys: a pair found by three keys is one candidate
    carrying three reasons, not three candidates. The reasons are kept because a pair surfaced only
    by `postal` is a different kind of evidence from one surfaced by `regid`.
    """
    materialised = list(records)
    index = index_by_key(materialised)
    by_key = {record.key: record for record in materialised}

    reasons: dict[tuple[str, str], list[str]] = defaultdict(list)
    for key, bucket in index.items():
        if len(bucket) < _MIN_BLOCK or len(bucket) > MAX_BLOCK_SIZE:
            continue
        for i, left in enumerate(bucket):
            for right in bucket[i + 1 :]:
                if left.key == right.key:
                    continue
                ordered = (left.key, right.key) if left.key < right.key else (right.key, left.key)
                reasons[ordered].append(key)

    for (left_key, right_key), keys in reasons.items():
        yield by_key[left_key], by_key[right_key], tuple(dict.fromkeys(keys))


def shares_a_key(left: CounterpartyRecord, right: CounterpartyRecord) -> tuple[str, ...]:
    """The keys two specific records share. Used to ask why a known pair was or was not surfaced."""
    return tuple(sorted(set(blocking_keys(left)) & set(blocking_keys(right))))


def token_overlap(left: CounterpartyRecord, right: CounterpartyRecord) -> float:
    """Jaccard overlap of normalised name tokens. Exposed here because blocking reports on it."""
    a, b = tokens(normalize_name(left.legal_name)), tokens(normalize_name(right.legal_name))
    union = a | b
    return len(a & b) / len(union) if union else 0.0
