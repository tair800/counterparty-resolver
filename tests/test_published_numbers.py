"""Every number in the README's result tables must be the one in the artifact.

A README is the only part of a repository most readers will check, and it is the easiest place for a
figure to go stale: a rule changes, the evaluation is re-run, and the table keeps the old number
because nothing was watching it. This parses the two result tables out of `README.md` and compares
each cell against `artifacts/evaluation.json`.

It is deliberately strict about the *arms* as well as the numbers. A table that quietly lost the row
where a baseline beats the system would pass a check that only verified the cells still present.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: The heading that introduces each result table, and the key it must agree with.
TABLES = {
    "### Development": "development",
    "### Held out": "holdout",
}

#: Every arm that has to appear. Dropping one is how a table stops being a comparison.
ARMS = ("exact_normalized_name", "fuzzy_name_only_0.90", "identifier_first", "system")


@pytest.fixture(scope="module")
def evaluation() -> dict[str, Any]:
    return dict(json.loads((ROOT / "artifacts" / "evaluation.json").read_text(encoding="utf-8")))


def _table_rows(heading: str) -> dict[str, list[str]]:
    """`{arm: [cells]}` for the markdown table that follows `heading`.

    Reads the first unbroken run of table lines after the heading. An earlier version cut at the
    first blank line measured *from the heading*, which is the blank line between the heading and
    the table, so it parsed nothing — and every assertion below it passed over an empty dict. That
    is the failure mode this whole file exists to prevent, so it is worth the two extra lines.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    after = readme[readme.index(heading) + len(heading) :]
    rows: dict[str, list[str]] = {}
    started = False
    for line in after.splitlines():
        if not line.startswith("|"):
            if started:
                break
            continue
        started = True
        if line.startswith("|---"):
            continue
        cells = [c.strip().strip("*").strip("`") for c in line.strip("|").split("|")]
        rows[cells[0]] = cells[1:]
    return rows


@pytest.mark.parametrize("heading", list(TABLES))
def test_the_table_still_names_every_arm(heading: str) -> None:
    """A comparison missing an arm is not a comparison, and the missing one is the awkward one."""
    rows = _table_rows(heading)

    missing = [arm for arm in ARMS if arm not in rows]

    assert not missing, f"{heading} lost: {missing}"


@pytest.mark.parametrize("heading", list(TABLES))
def test_every_published_metric_is_the_one_in_the_artifact(
    heading: str, evaluation: dict[str, Any]
) -> None:
    """Precision, recall and F1, cell by cell, to four decimal places."""
    arm_results = evaluation[TABLES[heading]]
    published = _table_rows(heading)
    wrong: list[str] = []

    for arm in ARMS:
        measured = arm_results["system"] if arm == "system" else arm_results["baselines"][arm]
        for offset, metric in enumerate(("precision", "recall", "f1")):
            claimed = published[arm][offset]
            actual = f"{measured[metric]:.4f}"
            if claimed != actual:
                wrong.append(
                    f"{heading} {arm} {metric}: README says {claimed}, artifact says {actual}"
                )

    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("heading", list(TABLES))
def test_the_false_merge_counts_are_the_ones_in_the_artifact(
    heading: str, evaluation: dict[str, Any]
) -> None:
    """The last column, and the one a reader will actually remember."""
    arm_results = evaluation[TABLES[heading]]
    published = _table_rows(heading)
    wrong: list[str] = []

    for arm in ARMS:
        measured = arm_results["system"] if arm == "system" else arm_results["baselines"][arm]
        claimed = published[arm][-1].replace(",", "")
        actual = str(measured["false_positives"])
        if claimed != actual:
            wrong.append(
                f"{heading} {arm}: README says {claimed} false merges, artifact says {actual}"
            )

    assert not wrong, "\n".join(wrong)


def test_the_candidate_generation_figures_match(evaluation: dict[str, Any]) -> None:
    """The number that caps every other number in the README."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    report = evaluation["candidate_generation"]

    for label, value in (
        ("recall", f"{report['recall']:.4f}"),
        ("reduction ratio", f"{report['reduction_ratio']:.6f}"),
        ("surfaced", f"{report['surfaced']:,}"),
        ("candidates generated", f"{report['candidates_generated']:,}"),
    ):
        assert value in readme, f"candidate generation {label} ({value}) is not in the README"


def test_the_readme_does_not_claim_a_hold_out_result_the_artifact_lacks() -> None:
    """The reverse direction: a table that exists in prose but not in evidence."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    evaluation = json.loads((ROOT / "artifacts" / "evaluation.json").read_text(encoding="utf-8"))

    claims_holdout = "### Held out" in readme

    assert claims_holdout == ("holdout" in evaluation), (
        "the README and the artifact disagree about whether the hold-out has been scored"
    )


def test_the_corpus_counts_in_the_readme_are_the_corpus_counts() -> None:
    corpus = json.loads((ROOT / "artifacts" / "corpus.json").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    counts = corpus["counts"]

    for label, value in (
        ("pairs", f"{counts['pairs']:,}"),
        ("positives", f"{counts['positives']:,}"),
        ("distinct records", f"{counts['distinct_records']:,}"),
    ):
        assert value in readme, f"corpus {label} ({value}) is not in the README"


def test_the_parser_actually_found_the_tables() -> None:
    """Guards the parser above.

    Every assertion in this file iterates over what `_table_rows` returned, so a parser that
    returned nothing would make all of them vacuous — which is precisely the class of defect this
    repository plants breaches for.
    """
    for heading, key in TABLES.items():
        rows = _table_rows(heading)
        assert len(rows) >= len(ARMS), (
            f"{heading} parsed {len(rows)} rows for {key}; the other assertions in this file are "
            "iterating over that, so they are currently checking nothing"
        )
