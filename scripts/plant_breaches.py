"""Break each guard on purpose, and fail if the suite stays green.

    uv run python scripts/plant_breaches.py
    uv run python scripts/plant_breaches.py --only ledger-update-trigger

A guard that has never been seen to reject anything is not a guard, it is a comment that runs.
Projects 3 and 4 in this portfolio both shipped one — project 4's host-header test could not fail,
because the request it sent was rejected for a different reason before the check it was testing ever
ran. Nobody noticed until a planted breach did.

So every claim this repository makes structurally gets a defect planted into it here, and the
harness asserts that the named test **fails**. A breach the suite survives is reported as a hole,
which is the useful output: it names a guarantee that is currently asserted by nothing.

Every edit is applied in place and reverted in a `finally`, and the run ends by checking that the
working tree is exactly as it was found. If that check fails, the process says so loudly — a
half-reverted breach is worse than no harness.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Breach:
    """One defect, where it goes, and which test has to notice it."""

    name: str
    guards: str
    path: str
    find: str
    replace: str
    expect_failure_in: list[str] = field(default_factory=list)
    #: Text the run has to print for the breach to count as caught. Needed where a red exit
    #: code is not enough on its own -- a session refused at collection exits non-zero, and
    #: so does a session where the guard did nothing and an unrelated test broke.
    #:
    #: The first version of this looked for the literal `UsageError`, which pytest never
    #: prints: it renders one as `ERROR: <message>`. The harness reported the guard as
    #: SURVIVED while the guard was in fact working, which is the same class of mistake the
    #: harness exists to catch, made one level up.
    expect_output: str = ""


BREACHES: tuple[Breach, ...] = (
    # -------------------------------------------------------------- the pre-registration guards
    Breach(
        name="lower-the-corpus-threshold",
        guards="ADR-001 kill criterion A cannot be weakened after the fact",
        path="tests/test_kill_criteria.py",
        find="MIN_LABELLED_PAIRS = 1_000",
        replace="MIN_LABELLED_PAIRS = 10",
        expect_failure_in=[
            "tests/test_predeclaration.py::test_the_thresholds_are_the_ones_adr_001_fixed"
        ],
    ),
    Breach(
        name="lower-the-candidate-recall-floor",
        guards="ADR-001 kill criterion E cannot be weakened after the fact",
        path="tests/test_kill_criteria.py",
        find="MIN_CANDIDATE_RECALL = 0.90",
        replace="MIN_CANDIDATE_RECALL = 0.10",
        expect_failure_in=[
            "tests/test_predeclaration.py::test_the_thresholds_are_the_ones_adr_001_fixed"
        ],
    ),
    Breach(
        name="skip-a-kill-test-by-mark",
        guards="no kill criterion can be disabled by a pytest mark",
        path="tests/test_kill_criteria.py",
        find="def test_F_the_system_beats_the_best_predeclared_baseline() -> None:",
        replace=(
            "@pytest.mark.skip(reason='planted breach')\n"
            "def test_F_the_system_beats_the_best_predeclared_baseline() -> None:"
        ),
        expect_output="disabled by a mark",
    ),
    Breach(
        name="reintroduce-importorskip",
        guards="the pre-registration skip cannot outlive the package it waited for",
        path="tests/test_kill_criteria.py",
        find="MIN_LABELLED_PAIRS = 1_000",
        replace="pytest.importorskip('counterparty_resolver')\n\nMIN_LABELLED_PAIRS = 1_000",
        expect_failure_in=[
            "tests/test_predeclaration.py::test_the_predeclaration_skip_does_not_outlive_the_package"
        ],
    ),
    Breach(
        name="delete-a-kill-criterion",
        guards="losing a predeclared condition is losing the criterion",
        path="tests/test_kill_criteria.py",
        find="def test_C_every_pair_carries_its_provenance() -> None:",
        replace="def test_C_provenance_renamed_away() -> None:",
        expect_failure_in=[
            "tests/test_predeclaration.py::test_the_kill_test_still_names_every_predeclared_condition"
        ],
    ),
    Breach(
        name="kill-test-stops-reading-the-artifact",
        guards="a component that grades itself is not evidence",
        path="tests/test_kill_criteria.py",
        find='    evaluation = _load("evaluation.json")\n    system = evaluation["development"]["system"]["precision"]',
        replace='    evaluation = {"development": {"system": {"precision": 1.0}, "baselines": {}}}\n    system = evaluation["development"]["system"]["precision"]',
        expect_failure_in=[
            "tests/test_predeclaration.py::test_the_kill_test_reads_committed_artifacts_rather_than_the_resolver"
        ],
    ),
    # ------------------------------------------------------------------ the brownfield constraint
    Breach(
        name="migration-alters-a-legacy-table",
        guards="source schemas are fixed; the resolution layer is additive",
        path="src/counterparty_resolver/store/migrations.py",
        find='        statements="ALTER TABLE merge_ledger ADD COLUMN approver_id TEXT;",',
        replace='        statements="ALTER TABLE legacy_gleif ADD COLUMN approver_id TEXT;",',
        expect_failure_in=[
            "tests/test_store.py::test_no_committed_migration_touches_a_legacy_table"
        ],
    ),
    Breach(
        name="drop-the-append-only-update-trigger",
        guards="the merge ledger is append-only in the database, not by convention",
        path="src/counterparty_resolver/store/schema.py",
        find="CREATE TRIGGER merge_ledger_is_append_only_update",
        replace="CREATE TRIGGER merge_ledger_planted_breach_never_fires\nBEFORE INSERT ON schema_version\nBEGIN\n    SELECT 1;\nEND;\n\nCREATE TRIGGER merge_ledger_unused_update",
        expect_failure_in=[
            "tests/test_store.py::test_the_ledger_refuses_update_and_delete_in_the_database"
        ],
    ),
    Breach(
        name="unmerge-leaves-the-source-link-behind",
        guards="an unmerge restores the exact prior state, including source-system linkage",
        path="src/counterparty_resolver/store/ledger.py",
        find='                "DELETE FROM source_link WHERE source = ? AND source_id = ? "\n                "AND linked_by_entry_id = ?",',
        replace='                "UPDATE source_link SET resolved_id = \'\' WHERE source = ? AND "\n                "source_id = ? AND linked_by_entry_id = ?",',
        expect_failure_in=[
            "tests/test_store.py::test_unmerge_restores_the_exact_prior_state_including_source_linkage"
        ],
    ),
    Breach(
        name="unmerge-drops-a-displaced-link",
        guards="a reversal restores a link the merge displaced, not just the link it created",
        path="src/counterparty_resolver/store/ledger.py",
        find='        for link in json.loads(original["displaced_links_json"] or "[]"):',
        replace="        for link in []:",
        expect_failure_in=[
            "tests/test_store.py::test_reversing_a_merge_restores_a_link_it_displaced"
        ],
    ),
    Breach(
        name="approver-identity-from-the-request",
        guards="the ledger records the identity the token bought, not one the client asserted",
        path="src/counterparty_resolver/api/app.py",
        find='    return "steward"',
        replace='    return str(request.headers.get("x-approver-id") or "steward")',
        expect_failure_in=["tests/test_api.py::test_the_ledger_records_the_tokens_identity"],
    ),
    Breach(
        name="migration-runs-outside-a-transaction",
        guards="a failed migration cannot leave the append-only trigger dropped",
        path="src/counterparty_resolver/store/migrations.py",
        find='    connection.execute("BEGIN IMMEDIATE")',
        replace='    connection.execute("SELECT 1")',
        expect_failure_in=[
            "tests/test_store.py::test_a_failed_migration_leaves_no_trace_of_itself"
        ],
    ),
    Breach(
        name="writer-names-a-fixed-set-of-columns",
        guards="the shipped writer works at every step of its own expand/contract rename",
        path="src/counterparty_resolver/store/ledger.py",
        find='    if "approved_by" in present:',
        replace="    if False:",
        expect_failure_in=[
            "tests/test_store.py::test_the_shipped_writer_works_at_every_step_of_the_rename"
        ],
    ),
    Breach(
        name="legacy-guard-ignores-row-changes",
        guards="the brownfield constraint covers a source system's rows, not only its shape",
        path="src/counterparty_resolver/store/migrations.py",
        find="_READ_ONLY = re.compile(",
        replace='_READ_ONLY = re.compile(  # type: ignore[assignment]\n    r"^.",',
        expect_failure_in=[
            "tests/test_store.py::test_the_guard_catches_every_way_of_writing_to_a_source_system"
        ],
    ),
    Breach(
        name="candidate-recall-counts-what-is-not-generated",
        guards="published candidate recall is the recall of the generator, not of key sharing",
        path="src/counterparty_resolver/evaluate.py",
        find="        if frozenset((pair.left.key, pair.right.key)) in emitted:",
        replace="        if shared:",
        expect_failure_in=[
            "tests/test_evaluation_method.py::test_candidate_recall_counts_only_emitted_pairs"
        ],
    ),
    Breach(
        name="migrations-declared-out-of-order",
        guards="the migration list reads in version order, so `migrate` applies all of it",
        path="src/counterparty_resolver/store/migrations.py",
        find="        version=5,",
        replace="        version=0,",
        expect_failure_in=["tests/test_store.py::test_the_migration_list_is_in_version_order"],
    ),
    Breach(
        name="contract-without-the-precondition",
        guards="the contract step refuses while a row would lose its approver",
        path="src/counterparty_resolver/store/migrations.py",
        find='        precondition="SELECT COUNT(*) FROM merge_ledger WHERE approver_id IS NULL",',
        replace="        precondition=None,",
        expect_failure_in=[
            "tests/test_store.py::test_the_contract_step_refuses_while_a_row_would_lose_its_approver"
        ],
    ),
    # ------------------------------------------------------------------------ the console's gate
    Breach(
        name="write-gate-fails-open",
        guards="a missing approver token disables writes; it never enables them",
        path="src/counterparty_resolver/api/app.py",
        find='    configured = os.environ.get("CR_APPROVER_TOKEN")\n    if not configured:',
        replace='    configured = os.environ.get("CR_APPROVER_TOKEN")\n    if not configured:\n        return "steward"\n    if False:',
        expect_failure_in=["tests/test_api.py::test_without_a_token_every_write_is_refused"],
    ),
    Breach(
        name="held-out-pair-reaches-the-console",
        guards="the hold-out is scored once and never browsed",
        path="artifacts/demo.json",
        find="__HOLDOUT_LEAK__",
        replace="__HOLDOUT_LEAK__",
        expect_failure_in=["tests/test_api.py::test_the_console_never_sees_a_held_out_pair"],
    ),
    # ----------------------------------------------------------------------- the decision itself
    Breach(
        name="identifier-agreement-ignores-distinctiveness",
        guards="an identifier held by many records names a family, not an entity",
        path="src/counterparty_resolver/features.py",
        find='            shared_by = (identifier_frequency or {}).get(f"{left_ra}|{left_id}", 1)',
        replace="            shared_by = 1",
        expect_failure_in=[
            "tests/test_resolver.py::test_an_identifier_shared_by_many_records_is_not_evidence_of_identity"
        ],
    ),
    Breach(
        name="name-agreement-outranks-the-registrar",
        guards="a registrar's two numbers outrank an identical name",
        path="src/counterparty_resolver/features.py",
        find="    left_id = normalize_identifier(left.registered_as)\n    right_id = normalize_identifier(right.registered_as)",
        replace=(
            "    if normalize_name(left.legal_name) == normalize_name(right.legal_name):\n"
            "        return (HardSignal.IDENTIFYING_NAME_AGREEMENT, 'planted breach')\n"
            "    left_id = normalize_identifier(left.registered_as)\n"
            "    right_id = normalize_identifier(right.registered_as)"
        ),
        expect_failure_in=[
            "tests/test_resolver.py::test_a_registrars_number_outranks_an_identical_name"
        ],
    ),
    Breach(
        name="the-score-asserts-a-match-again",
        guards="ADR-002: the weighted score never asserts a match on its own",
        path="src/counterparty_resolver/resolve.py",
        find="THRESHOLD_MATCH: float | None = None",
        replace="THRESHOLD_MATCH: float | None = 0.86",
        expect_failure_in=[
            "tests/test_resolver.py::test_the_weighted_score_never_asserts_a_match_on_its_own"
        ],
    ),
    Breach(
        name="silently-accept-a-new-known-limitation",
        guards="the set of accepted defects cannot grow by setting a flag",
        path="tests/adversarial_cases.json",
        find='   "id": "diacritic-drift",',
        replace='   "id": "diacritic-drift",\n   "known_limitation": true,',
        expect_failure_in=[
            "tests/test_adversarial.py::test_the_known_limitations_are_exactly_the_ones_declared"
        ],
    ),
)


def _leak_a_holdout_pair(text: str) -> str:
    """Put a genuinely held-out pair into the console's dataset.

    Built rather than written as a literal, because the id has to be one the committed split
    actually holds out -- a made-up id would fail the test for the wrong reason.
    """
    split = json.loads((ROOT / "artifacts" / "holdout.json").read_text(encoding="utf-8"))
    demo = json.loads(text)
    leaked = dict(demo["pairs"][0])
    leaked["pair_id"] = split["holdout_pair_ids"][0]
    demo["pairs"].append(leaked)
    return json.dumps(demo, indent=1) + "\n"


def _apply(breach: Breach, text: str) -> str:
    """The breached contents of one file.

    Line endings are normalised to LF first. This repository is worked on under Windows and
    some files are stored CRLF, so a multi-line anchor written as a Python literal matched in
    some files and silently failed in others -- and a breach that fails to apply is a guard
    left unproven, which is the thing this script exists to prevent.
    """
    text = text.replace("\r\n", "\n")
    if breach.name == "held-out-pair-reaches-the-console":
        return _leak_a_holdout_pair(text)
    if breach.find not in text:
        raise SystemExit(
            f"breach {breach.name!r} does not apply: its anchor is no longer in {breach.path}. "
            "The harness is stale, which means the guard it covers is currently unproven."
        )
    return text.replace(breach.find, breach.replace, 1)


def _git_status() -> str:
    """The porcelain status, or an exception.

    `check=True` matters more here than anywhere else in the repository. Both callers previously
    read only `stdout` with `check=False`, so a git that failed for any reason -- not a repository,
    a locked index, a broken HEAD -- returned an empty string, which reads as "clean". The harness
    that guards every other guard would then have verified neither its precondition nor its
    postcondition and still printed "the working tree is unchanged".
    """
    # S603/S607: fixed argv, no shell, `git` from PATH on purpose.
    return subprocess.run(
        ["git", "status", "--porcelain"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _run(node_ids: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "pytest", "-x", "-q", "--no-header", *node_ids],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="run one breach by name")
    arguments = parser.parse_args()

    selected = [b for b in BREACHES if not arguments.only or b.name == arguments.only]
    if not selected:
        raise SystemExit(f"no breach named {arguments.only!r}")

    # S607: `git` is resolved from PATH on purpose. Pinning an absolute path would make
    # this script work on one machine, and the command is a fixed argv with no shell.
    dirty = _git_status()
    if dirty:
        print(
            "working tree is not clean; the revert check at the end cannot mean anything:\n" + dirty
        )
        return 1

    holes: list[Breach] = []
    print(f"planting {len(selected)} breaches\n")

    for breach in selected:
        path = ROOT / breach.path
        original = path.read_bytes()
        try:
            path.write_text(
                _apply(breach, original.decode("utf-8")), encoding="utf-8", newline="\n"
            )
            result = _run(breach.expect_failure_in or ["tests/test_kill_criteria.py"])
            noticed = result.returncode != 0
            if breach.expect_output:
                noticed = noticed and breach.expect_output in result.stdout + result.stderr
        finally:
            path.write_bytes(original)

        mark = "caught" if noticed else "SURVIVED"
        print(f"  [{mark:>8}] {breach.name}")
        print(f"             guards: {breach.guards}")
        if not noticed:
            holes.append(breach)
            tail = (result.stdout + result.stderr).strip().splitlines()[-2:]
            for line in tail:
                print(f"             pytest said: {line}")

    still_dirty = _git_status()
    if still_dirty:
        print("\nTHE WORKING TREE WAS NOT RESTORED. Revert it before doing anything else:")
        print(still_dirty)
        return 2

    if holes:
        print(f"\n{len(holes)} breach(es) survived. Each one names a guarantee nothing asserts:")
        for breach in holes:
            print(f"  - {breach.name}: {breach.guards}")
        return 1

    print(f"\nall {len(selected)} breaches were caught, and the working tree is unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
