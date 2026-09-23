# CLAUDE.md — counterparty-resolver

Operating rules for this repository. Read this and `DECISIONS.md` before changing anything.

---

## 1. What this is

Decide whether two records refer to the same real-world counterparty, on labels a registrar wrote.
Deterministic rules over interpretable features produce MATCH / REVIEW / NO_MATCH; a person approves
every merge; every merge is reversible including source-system linkage.

---

## 2. The rule that overrides every other rule in this file

**The hold-out was scored once, at `b67b83e`. No rule may change on the basis of that number.**

ADR-001 fixes it and `DECISIONS.md` ADR-003 records it. In practice this means: do not edit
`normalize.py`, `blocking.py`, `features.py` or `resolve.py` to improve a metric. Not the weights,
not the thresholds, not a hard signal, not a legal-form spelling, not a blocking key.

Three adversarial cases currently fail and are marked `known_limitation`. **They stay failing.**
Fixing them means changing a rule against a spent hold-out, which would silently invalidate every
number in the README.

A genuine bug — a crash, a type error, a doc that describes behaviour the code does not have — is
not a rule change and may be fixed. If in doubt: does this alter what the resolver decides about any
pair? If yes, it is a rule change and needs a new hold-out, which needs a new corpus.

---

## 3. What is asserted, and where

Do not weaken any of these. Each is currently proven to be able to fail — `make breaches` plants a
defect into all sixteen and reports any that survive.

| claim | asserted by |
|---|---|
| ADR-001's six kill criteria | `tests/test_kill_criteria.py`, against committed artifacts |
| the kill test cannot disable itself | `tests/conftest.py` (session refusal) + `tests/test_predeclaration.py` (AST) |
| legacy schemas are never altered | `tests/test_store.py`, over the committed migration list |
| the ledger is append-only | database triggers, exercised in `tests/test_store.py` |
| unmerge restores exact prior state | full-table snapshot comparison |
| the contract step refuses early | `tests/test_store.py` |
| the write gate fails closed | `tests/test_api.py`, against the endpoint |
| the hold-out never reaches the console | `tests/test_api.py` |
| the resolver's individual behaviours | `tests/test_resolver.py`, `tests/test_adversarial.py` |

**A guard that has never been seen to reject anything is not a guard.** If you add one, add a breach
for it in `scripts/plant_breaches.py` in the same commit.

---

## 4. Commands

```bash
make check              # lint, types, tests. No network. This is the CI gate.
make breaches           # plant a defect into each guard; fails if any survives
make artifacts          # corpus, split, development evaluation, demo dataset
make evaluate-holdout   # ONE-WAY DOOR. See §2.
make run                # the console, read-only
make screenshots        # regenerate docs/screenshots, light and dark
```

`make breaches` refuses to run on a dirty working tree, because its final check is that the tree is
unchanged. Commit first.

---

## 5. Conventions

- Typed Python, `mypy --strict` over `src`, `tests` and `scripts`. No `Any` that a real type fits.
- `ruff` at 100 columns. Every `# noqa` carries the reason on the same line or above it.
- Docstrings say **why**, not what. A comment restating the code is worse than no comment.
- Pydantic models are frozen and forbid extra fields.
- Every feature returns a value **and** a sentence a person can read. ADR-001 requires evidence
  somebody can disagree with, not a number they can only compare with a cut-off.
- Tests are named for the behaviour, not the function. `test_a_registrars_number_outranks_an_identical_name`,
  not `test_hard_signal_2`.

## 6. Data and secrets

- `data/` is gitignored and holds raw fetched payloads. Nothing from it is ever committed.
- `artifacts/` **is** committed: it is the evidence, and GLEIF is CC0 so redistribution is permitted.
- No token, key or password in the repository. `CR_APPROVER_TOKEN` comes from the environment; a
  missing one means read-only, never open.
- `docs/DATA_PROVENANCE.md` is the authority on what came from where under which licence. Update it
  before adding a source, not after.

## 7. What is not here, and why

Recorded in `DECISIONS.md` and the README's "Not built" table: no pgvector, no Redis, no live model,
no React, no PostgreSQL. None of those is a gap discovered later; each was a decision with a reason.
If you add one, the reason goes in `DECISIONS.md` as a new ADR before the code lands.

**Never claim a capability that does not exist.** Every number in the README is read from
`artifacts/evaluation.json`, and the console renders the same file rather than a copy of it.
