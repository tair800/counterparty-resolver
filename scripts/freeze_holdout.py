"""Write the held-out split definition. Run ONCE, committed, and only then scored.

    uv run python scripts/freeze_holdout.py

Prints the shape of the split and nothing about how anything scores on it, deliberately: this
script must be runnable, and its output readable, without anyone learning a result.
"""

from __future__ import annotations

import json
from pathlib import Path

from counterparty_resolver.corpus.holdout import split_corpus

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


def main() -> int:
    corpus = json.loads((ARTIFACTS / "corpus.json").read_text(encoding="utf-8"))
    split = split_corpus(corpus)

    out = ARTIFACTS / "holdout.json"
    out.write_text(json.dumps(split, indent=1) + "\n", encoding="utf-8")

    print(f"wrote {out}")
    print(
        f"  clusters       : {split['clusters']['total']} ({split['clusters']['holdout']} held out)"
    )
    print(
        f"  development    : {split['development']['pairs']} pairs "
        f"({split['development']['positives']}+ / {split['development']['negatives']}-)"
    )
    print(
        f"  holdout        : {split['holdout']['pairs']} pairs "
        f"({split['holdout']['positives']}+ / {split['holdout']['negatives']}-)"
    )
    print(f"  dropped (straddling): {split['dropped_for_straddling']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
