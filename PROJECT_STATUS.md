# PROJECT_STATUS — counterparty-resolver

Updated 2026-09-24.

## Milestone

**Evidence complete and frozen. The hold-out has been scored.**

`b67b83e` is the line nothing may cross. ADR-001 fixes that no rule, threshold, feature,
normalisation or blocking key changes on the basis of that score, so the resolver is closed to
changes that alter what it decides about any pair. Documentation, tooling, tests and the console may
still change.

## Verified

Every row below was run, not assumed. Commands are in `Makefile`.

| | result |
|---|---|
| `make check` (ruff, ruff format, mypy --strict, pytest) | green — **132 tests**, 34 files type-clean |
| `make breaches` | **24 of 24 planted defects caught**, working tree restored |
| Docker image builds, serves, refuses a write | 263 MB, runs as uid 10001, `/health` 200, POST without a token → 403 |
| GitHub Actions `check` + `container` | green on `main` |
| Console, all four screens, light and dark | captured to `docs/screenshots/` by `scripts/screenshots.py` |
| Kill criteria A–F | all pass against the committed artifacts |

## The numbers, as published

| arm | dev precision | dev recall | dev F1 | held-out precision | held-out F1 |
|---|---|---|---|---|---|
| `exact_normalized_name` | 0.9973 | 0.4877 | 0.6550 | 0.9967 | 0.6572 |
| `fuzzy_name_only_0.90` | 0.5936 | 0.6306 | 0.6115 | 0.8890 | 0.7488 |
| `identifier_first` | 0.9855 | 0.6194 | **0.7607** | 0.9817 | 0.7538 |
| **system** | **0.9980** | 0.5758 | 0.7303 | **0.9986** | 0.7344 |

Corpus: 12,984 pairs (6,492 adjudicated duplicates, 6,492 mined hard negatives), 12,853 records.
Split: 6,361 clusters, 1,203 held out, 0 pairs dropped for straddling.
Candidate generation: recall 0.9223, reduction ratio 0.995521.

## Two independent reviews, and what they changed

Run against `2345e65`: one hiring-engineer pass, one security and correctness pass. Between them
they found **four false claims, two real bugs in the merge ledger and its migrations, and three
undisclosed properties of the corpus**. All of it is in `DECISIONS.md` ADR-004, and all of it is
fixed or disclosed. The pattern is worth stating plainly: the defects were almost entirely in prose,
and almost none in the enforced guarantees — which is the predictable failure of a repository that
spends its effort on mechanisms and then writes sentences beside them.

The three that mattered most:

1. **A failed migration could permanently destroy the append-only trigger.** `executescript` commits
   before it runs; the "same transaction" the comment claimed did not exist. All DDL now runs inside
   an explicit `BEGIN IMMEDIATE`.
2. **The shipped writer could not write during its own expand/contract rename** — `approved_by` is
   `NOT NULL` and the writer named a fixed column list — and the test named for proving otherwise
   asserted column names and then wrote through the old shape.
3. **Published candidate recall was 0.9223 and is 0.9138.** It measured key-sharing; blocking skips
   blocks over `MAX_BLOCK_SIZE`, so 45 pairs were counted as surfaced that are never generated.

Two new suites exist because of this: `tests/test_published_numbers.py` fails the build when a cell
in the README disagrees with the artifact, and `tests/test_evaluation_method.py` asserts that the
quantities compared to ADR-001's thresholds are the quantities those thresholds name.

## Known issues, carried deliberately

1. **`identifier_first` beats the system on F1** (0.7607 against 0.7303). The predeclared criterion
   was precision and the system wins there — 6 false merges against 48 — but not on the aggregate.
2. **The precision margin is one false merge.** 0.9980 against 0.9973 is six against seven.
3. **Three adversarial cases ship failing**, marked `known_limitation`: a suffix present on one side
   only, `P/L` against `Pty Ltd`, and `IBM` against `International Business Machines`. Each is a
   rule change against a spent hold-out, so each is published as a miss.
4. **Identifier conflict costs 405 adjudicated duplicates** and is not fixable on this evidence: a
   Spanish registry renumbering and two SEC series sharing a name look identical in the fields
   available.
5. **The negative class contains unadjudicated duplicates** by construction. Published precision is
   a floor, not an estimate.
6. **The hold-out's negatives are easier than development's** (mean name similarity 0.8147 against
   0.9037), so its absolute precision is not comparable. The development number is the one quoted.
7. **The frequency tables are computed over both splits.** Not label leakage, but information from
   the held-out records reaches their own scoring. Both arms are published: hold-out precision
   0.9986 with corpus-wide priors, **0.9972 with development-only priors**, which is the floor.
8. **`fuzzy_name_only_0.90` is scored by a corpus selected against it** — hard negatives are ranked
   by that baseline's own decision function. Kill test F turns on `identifier_first`, which the
   ranking never touches, so the criterion is unaffected.
9. **Prior names are carried and never consulted.** No feature reads `other_names`, though 324 of
   the 2,234 unmatched development positives have an exact normalised alias match against one such
   collision in 5,266 negatives. The clearest single recall win, and it needs a new hold-out.
10. **No `p50/p95` latency and no cost-per-operation.** Nothing here calls a model or an external
    service at request time, so the second would be zero; the first is simply not measured.

None of these is a defect to fix in this increment. Each is in `DECISIONS.md` and the README.

## Blockers

**One, and it needs the owner.** The Render blueprint (`render.yaml`) is committed and the image is
verified, but creating the free web service is an outward-facing action on the owner's account.
Everything up to the click is done:

- `render.yaml`: free plan, Frankfurt, Docker runtime, `/health` as the liveness probe.
- **No `CR_APPROVER_TOKEN` is declared**, so the deployed console is read-only and every write
  endpoint answers 403. That is the demo mode, and it is the default rather than a setting.
- No database, no paid resource, no model, no external call at request time.

## Not built, with the reason

In the README's own table and in `DECISIONS.md`: no pgvector, no Redis, no live model adjudication,
no React, no PostgreSQL. Each was a decision for this 1–2 day increment, not an omission.

The one that costs the most: **the hybrid arm is not run**, so no USD-per-1,000-pairs comparison
between deterministic-only and band-adjudicated is published. The band itself is measured — 21.2% of
development, 36.4% of it genuine duplicates — which is the input that decision would need.

## Next, if this project is picked up again

In order of value:

1. **A second corpus, and a second hold-out.** Every limitation above is now locked by ADR-001.
   Using `other_names`, fixing the three adversarial misses, adding `p l` to the legal-form table or
   reweighting `acronym_match` all require a fresh split, and a fresh split requires a fresh corpus.
   Draw the negatives with a metric no arm decides on, and from a record pool that is not entirely
   duplicate-participating — both are disclosed limitations a rebuild can simply remove.
2. **The hybrid arm**, with the cost comparison the blueprint asks for. The band is measured and
   the plumbing to route it exists; what is missing is the adjudicator and the cost accounting.
3. **Adjudicated negatives.** The precision floor is set by unadjudicated duplicates in the negative
   class. A second label source with explicit non-duplicate assertions would lift it.
4. **Postgres**, if a second writer ever exists. The ledger's guarantees are schema-level and port
   unchanged.

## Git

`main` at `github.com/tair800/counterparty-resolver`. The ordering that makes the evaluation
checkable, in `git log`:

| commit | what it fixed |
|---|---|
| `b620131` | the claim, corpus contract, baselines, hold-out policy, kill test — zero source files |
| `bb02786` | the corpus and the frozen split — zero evaluation files |
| `549ab39` | the decision rules and a development-only evaluation — no `holdout` key in the artifact |
| `b67b83e` | the hold-out, scored once |
