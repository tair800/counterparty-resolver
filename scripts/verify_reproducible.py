"""ADR-001 kill criterion D: a rebuild must not change a label.

    uv run python scripts/verify_reproducible.py

Run after `build_corpus.py --refresh`. It compares the corpus now on disk against the one committed
in git, which is the only version anybody reading this repository can check, and reports the first
labels that moved.

**Labels, not bytes.** `generated_at` changes on every run and the token frequency table shifts when
GLEIF publishes new records, and neither is a reproducibility failure. What may not move is the
adjudication: a pair that was `match` yesterday and is `no_match` today means the corpus does not
reproduce, and every number derived from it is unrepeatable.

New pairs appearing is expected — GLEIF adjudicates duplicates continuously — and is reported rather
than failed. A pair *disappearing* is reported too, because a shrinking corpus is worth knowing
about even though a registrar withdrawing an adjudication is a legitimate reason for it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

#: How many disagreeing pairs to print. Enough to see the pattern, not enough to bury it.
_EXAMPLES = 20


def _committed(path: str, ref: str) -> dict[str, Any]:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "show", f"{ref}:{path}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"cannot read {path} at {ref}: {result.stderr.decode(errors='replace')}")
    return dict(json.loads(result.stdout.decode("utf-8")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD", help="the git revision to compare against")
    parser.add_argument("--path", default="artifacts/corpus.json")
    arguments = parser.parse_args()

    rebuilt = json.loads((ROOT / arguments.path).read_text(encoding="utf-8"))
    committed = _committed(arguments.path, arguments.ref)

    before = {p["pair_id"]: p["label"] for p in committed["pairs"]}
    after = {p["pair_id"]: p["label"] for p in rebuilt["pairs"]}

    changed = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())

    print(f"committed at {arguments.ref}: {len(before)} pairs")
    print(f"rebuilt on disk            : {len(after)} pairs")
    print(f"  labels changed : {len(changed)}")
    print(f"  pairs added    : {len(added)}   (GLEIF adjudicates continuously; expected)")
    print(f"  pairs removed  : {len(removed)}")

    if changed:
        print("\nADR-001 kill criterion D: the corpus does not reproduce. First disagreements:")
        by_id = {p["pair_id"]: p for p in rebuilt["pairs"]}
        for pair_id in changed[:_EXAMPLES]:
            pair = by_id[pair_id]
            print(
                f"  {pair_id}  {before[pair_id]} -> {after[pair_id]}  "
                f"{pair['left']['legal_name'][:38]!r} / {pair['right']['legal_name'][:38]!r}"
            )
        return 1

    print("\nno label changed: the corpus reproduces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
