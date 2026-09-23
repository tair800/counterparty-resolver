"""The guard that stops the kill test from being predeclared and then quietly never run.

Two layers, because one is not enough. `conftest.py` asks pytest what it is about to run — that is
the guard. This file is the second line: it catches what collection cannot see, such as a scenario
deleted from the file or an assertion that stops reading the artifacts.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KILL_TEST = ROOT / "tests" / "test_kill_criteria.py"

#: The conditions ADR-001 fixed before implementation. Declared once; both guards read it.
_CONDITIONS = (
    "test_A_at_least_one_thousand_usable_labelled_pairs",
    "test_A_both_labels_are_present_in_quantity",
    "test_B_the_duplicates_exhibit_the_claimed_variant_types",
    "test_C_every_pair_carries_its_provenance",
    "test_D_the_corpus_declares_what_it_was_built_from",
    "test_E_candidate_generation_keeps_the_true_matches",
    "test_F_the_system_beats_the_best_predeclared_baseline",
    "test_F_the_system_is_not_beating_the_baseline_by_abstaining",
)


def test_the_kill_test_still_names_every_predeclared_condition() -> None:
    """ADR-001 fixes six conditions across eight assertions. Losing one is losing the criterion."""
    source = KILL_TEST.read_text(encoding="utf-8")
    missing = [name for name in _CONDITIONS if name not in source]

    assert not missing, f"the predeclared kill test lost: {', '.join(missing)}"


def test_the_predeclaration_skip_does_not_outlive_the_package() -> None:
    """Once `counterparty_resolver` imports, the kill test must actually run."""
    if importlib.util.find_spec("counterparty_resolver") is None:
        pytest.skip("the package does not exist yet; the kill test is still predeclared")

    source = KILL_TEST.read_text(encoding="utf-8")
    banned = ["importorskip", "mark.skip", "skipif", "pytest.skip("]
    found = [needle for needle in banned if needle in source]

    assert not found, (
        "counterparty_resolver is importable, so nothing in the kill test may disable itself. "
        f"Found: {', '.join(found)}."
    )


def test_the_thresholds_are_the_ones_adr_001_fixed() -> None:
    """The numbers themselves, so lowering one is a visible edit to a test rather than a tweak."""
    source = KILL_TEST.read_text(encoding="utf-8")
    tree = ast.parse(source)
    constants = {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }

    assert constants.get("MIN_LABELLED_PAIRS") == 1_000, constants.get("MIN_LABELLED_PAIRS")
    assert constants.get("MIN_CANDIDATE_RECALL") == 0.90, constants.get("MIN_CANDIDATE_RECALL")
    assert constants.get("VARIANT_SAMPLE") == 200, constants.get("VARIANT_SAMPLE")


def test_the_kill_test_reads_committed_artifacts_rather_than_the_resolver() -> None:
    """ADR-001: 'a component that grades itself is not evidence.'

    Every condition must be asserted against a file on disk. A kill test that imported the scorer
    and asked it how it did would pass against a scorer that reported success and measured nothing.
    """
    tree = ast.parse(KILL_TEST.read_text(encoding="utf-8"))
    bodies = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    silent = []
    for name in _CONDITIONS:
        node = bodies.get(name)
        if node is None:
            continue
        loads = [
            child
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_load"
        ]
        if not loads:
            silent.append(name)

    assert not silent, f"these conditions no longer read a committed artifact: {', '.join(silent)}"
