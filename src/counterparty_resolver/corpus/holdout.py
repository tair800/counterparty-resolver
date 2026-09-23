"""The held-out split. Defined here, committed, and only then scored.

Project 3 published a development F1 of 0.963 and a held-out F1 of 0.328. The gap was not a bug in
the model; it was the absence of this file. So the order is fixed and provable from git: this module
and the split it produces are committed **before** `evaluate.py` is ever pointed at the hold-out.

**The split is over entity clusters, not over pairs.** Splitting pairs at random puts the same
LEI on both sides, and a rule tuned until it matched `NORDIC CAPITAL SP Z OO` on development is
then congratulated for matching the same company on the held-out side. A cluster is a connected
component of the adjudication graph -- A merged into B, B merged into C -- so the whole component
lands on one side or the other, and a pair that would straddle the boundary is **dropped** rather
than assigned. Dropping is the conservative choice: it shrinks the hold-out and cannot inflate it.

Assignment is by hash of the cluster's root LEI, not by a shuffle. The same corpus produces the same
split on any machine, in any Python, forever -- which is what makes "frozen before scoring" a
checkable claim rather than a promise.
"""

from __future__ import annotations

import hashlib
from typing import Any

__all__ = ["HOLDOUT_FRACTION", "Split", "assign_split", "split_corpus"]

#: About a fifth. Large enough that the number means something, small enough that the development
#: corpus is still the thing the system was built on.
HOLDOUT_FRACTION = 0.20

#: Mixed into the hash so the split is this project's rather than a property of LEI strings.
_SALT = "counterparty-resolver/holdout/v1"


class Split(dict[str, Any]):
    """The committed split: which clusters are held out, and where each pair landed."""


def _clusters(pairs: list[dict[str, Any]]) -> dict[str, str]:
    """Record id -> cluster root, over the adjudication graph.

    Only positive pairs create edges. A negative asserts two records are *not* the same entity, so
    letting it join clusters would merge the very things the corpus says are distinct.
    """
    parent: dict[str, str] = {}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != root:
            parent[item], item = root, parent[item]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for pair in pairs:
        parent.setdefault(pair["left_id"], pair["left_id"])
        parent.setdefault(pair["right_id"], pair["right_id"])
        if pair["label"] == "match":
            union(pair["left_id"], pair["right_id"])

    return {item: find(item) for item in parent}


def assign_split(cluster_root: str, *, fraction: float = HOLDOUT_FRACTION) -> str:
    """`"holdout"` or `"development"`, deterministically, from the cluster root alone.

    A hash rather than a shuffle so that the assignment is reproducible without carrying a seed, a
    random state or an ordering assumption. Re-deriving the split is how anyone checks that the
    committed one was not quietly re-drawn after a disappointing score.
    """
    digest = hashlib.sha256(f"{_SALT}|{cluster_root}".encode()).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "holdout" if bucket < fraction else "development"


def split_corpus(corpus: dict[str, Any], *, fraction: float = HOLDOUT_FRACTION) -> Split:
    """Assign every pair to development, holdout, or dropped-for-straddling."""
    pairs = corpus["pairs"]
    roots = _clusters(pairs)

    side_of_cluster = {
        root: assign_split(root, fraction=fraction) for root in sorted(set(roots.values()))
    }

    development: list[str] = []
    holdout: list[str] = []
    dropped: list[str] = []

    for pair in pairs:
        left_side = side_of_cluster[roots[pair["left_id"]]]
        right_side = side_of_cluster[roots[pair["right_id"]]]
        if left_side != right_side:
            dropped.append(pair["pair_id"])
        elif left_side == "holdout":
            holdout.append(pair["pair_id"])
        else:
            development.append(pair["pair_id"])

    def counts(ids: list[str]) -> dict[str, int]:
        by_id = {p["pair_id"]: p for p in pairs}
        return {
            "pairs": len(ids),
            "positives": sum(1 for i in ids if by_id[i]["label"] == "match"),
            "negatives": sum(1 for i in ids if by_id[i]["label"] == "no_match"),
        }

    return Split(
        {
            "policy": (
                "Group-aware over connected components of the adjudication graph. A pair "
                "whose two records fall in different components is DROPPED rather than "
                "assigned, so no entity appears on both sides. Assignment is "
                "sha256(salt|cluster_root) < fraction, so the split is reproducible without "
                "a seed and cannot be quietly re-drawn."
            ),
            "fraction": fraction,
            "salt": _SALT,
            "clusters": {
                "total": len(side_of_cluster),
                "holdout": sum(1 for s in side_of_cluster.values() if s == "holdout"),
                "development": sum(1 for s in side_of_cluster.values() if s == "development"),
            },
            "development": counts(development),
            "holdout": counts(holdout),
            "dropped_for_straddling": len(dropped),
            "development_pair_ids": sorted(development),
            "holdout_pair_ids": sorted(holdout),
        }
    )
