# Decisions

Architecture decision records. **ADR-001 is written before any implementation** and fixes the claim,
the corpus contract, the baselines, the hold-out policy and the kill test. Nothing in it may be
weakened after a result is seen.

---

## ADR-001 — The claim, the corpus, the baselines, and the kill test

**Status:** accepted, 2026-09-24, **before implementation**.

### The claim

> **Two records refer to the same counterparty, or they do not, and the system says which on evidence
> a person can check.** The labels are not ours: they are duplicate adjudications made by LEI Issuing
> Organisations over real business entities. The decision is deterministic code over interpretable
> features, with a third answer — **REVIEW** — because a wrong merge is worse than an unresolved pair.

### The failure this prevents

The same counterparty exists more than once across CRM, ERP and billing under legal-name variants,
transliterations, stale addresses and changed registrations. Invoices go to the wrong record and
credit exposure is understated. **A bad merge is worse than a duplicate**: unpicking it by hand takes
days and corrupts payment routing, so the system must be able to decline to decide.

### Ground truth — where the labels come from, and why they are not ours

| Source | Licence | Role | Vendored? |
|---|---|---|---|
| [GLEIF LEI](https://www.gleif.org/en/lei-data/gleif-golden-copy) | **CC0 1.0** | Primary labels. Records whose registration status is `DUPLICATE` carry `entity.successorEntity.lei` — a human adjudication by an LEI Issuing Organisation that two LEIs are the same entity | **Fetched**, and redistributable if we chose to |
| [Companies House Free Company Data](https://download.companieshouse.gov.uk/en_output.html) | **OGL v3.0** | Second immutable schema, for the brownfield crosswalk | Fetched, attribution required and given |

**A positive pair is never created because two strings look alike.** It exists because a registrar
recorded one LEI as a duplicate of another. Nothing in this repository may author a positive label.

**Negatives are constructed deterministically and the rule is fixed here**: pairs that survive
candidate generation — i.e. are similar enough to be *considered* — but are not linked by any
`successorEntity` relationship, transitively. Hard by construction: a negative that no blocking key
ever surfaces teaches nothing and inflates every metric.

### What changed from the blueprint, checked today rather than assumed

`PORTFOLIO_BLUEPRINT.md` names a third source — **OpenSanctions Pairs**, CC BY-NC 4.0, fetch-only,
with a published 91.33% F1 rule-based baseline as an *external* comparison point. **That dataset is
gone.** `https://www.opensanctions.org/docs/pairs/` returns 404, as do
`/datasets/pairs/`, `/docs/data/pairs/` and `data.opensanctions.org/contrib/pairs/pairs.json`, and
the live dataset index at `data.opensanctions.org/datasets/latest/index.json` lists **479 datasets,
none of them pairs-like**. Verified 2026-09-24.

**Consequence, stated rather than absorbed:** this project's numbers are **not externally
comparable to a published baseline**. They remain externally *labelled* — the adjudications are
GLEIF's — which is the part the claim rests on. The README must not imply the stronger property.

### Candidate generation is measured separately, and its recall caps everything

A resolver cannot recover a true match that blocking never surfaced. **Candidate recall is reported
as its own number**, before and independently of precision and recall on the decision. A system with
0.99 precision over a candidate set that dropped a third of the true pairs has not resolved anything.

### Decision bands, and how abstention is scored

Three outcomes: **MATCH**, **REVIEW**, **NO_MATCH**. Fixed now, before any threshold is chosen:

- **Precision and recall are computed over the decisions actually made** — MATCH and NO_MATCH — and
  the **REVIEW rate is published beside them, never folded into either.**
- A REVIEW on a true positive is **not** counted as a recall success. A REVIEW on a true negative is
  **not** counted as a precision success. Abstention buys accuracy at a stated price and the price is
  always shown.
- **The headline number is precision**, because the asymmetry is real: a false MATCH merges two
  legally distinct counterparties.

### The baselines, chosen before any result

Three, each what a competent engineer would actually reach for:

1. **Exact normalized-name match** — the same normalization the system uses, then string equality.
2. **Fuzzy-name-only threshold** — one similarity score over normalized names, one cut-off.
3. **Identifier-first deterministic** — same registration authority *and* same `registeredAs` is a
   match; otherwise fall back to (1).

Not straw men: (3) is what a careful person writes first, and the system must beat it or be dropped.

### Train / development / hold-out — fixed before scoring

Project 3 published a development F1 of 0.963 and a held-out F1 of 0.328. That is the reason this
section exists.

- A **held-out slice is frozen in git before it is scored**, and the freeze is provable by commit
  order rather than asserted.
- The split is **group-aware**: every LEI belonging to one duplicate-successor cluster lands wholly
  on one side. The same entity must never appear on both sides of the split, or the task is
  artificially easy and the number is meaningless.
- **After the hold-out is scored, no matching rule, threshold, feature or normalisation changes.**
  A defect found afterwards is recorded as future work. The slice is spent.

### The kill test — predeclared, and not to be weakened

The project **fails** if any of these is true:

| | Condition | Threshold |
|---|---|---|
| **A** | Usable labelled pairs after cleaning | **< 1,000** |
| **B** | GLEIF `DUPLICATE` records do not exhibit the claimed variant types on inspection | any of: legal-form abbreviation variance, diacritic/casing drift, or the same entity under two registration authorities, absent from a sampled 200 |
| **C** | Provenance incomplete | any pair lacking source, both ids, label basis, and source revision |
| **D** | The corpus does not reproduce | a rebuild changes any label |
| **E** | Candidate generation drops true matches | candidate recall **< 0.90** on the development corpus |
| **F** | The system does not beat the best predeclared baseline | system precision **≤** best baseline precision on the **same** pairs |

A and B are the authoritative criterion from `PORTFOLIO_BLUEPRINT.md`. C–F are this repository's
own, added here so they are fixed before results exist rather than after.

**Every count is computed from the committed artifacts by `scripts/gate.py`, not asserted in prose.**

### Where AI is allowed

The blueprint specifies a hybrid arm: a model adjudicating a measured ambiguity band. **No live model
is required for any number this project publishes**, and none is in the decision path. The ambiguity
band is measured regardless, because its size is the honest input to whether a model is worth buying.

A model may **explain** a decision already made. It may not create a label, override an identifier,
turn REVIEW into MATCH, or determine evaluation truth.

### What this build does not include, against the blueprint

Recorded now so it cannot look like an omission discovered later. The blueprint specifies ~9 sessions;
the owner's instruction for this increment is a 1–2 day fast-track that explicitly forbids Redis and
says not to add PostgreSQL where files suffice.

| Blueprint item | Status |
|---|---|
| pgvector + embedding candidate generation | **Not built.** Deterministic blocking instead, and its recall is published as kill test E rather than assumed. |
| Redis blocking keys / candidate cache | **Not built.** |
| Live LLM adjudication of the ambiguity band | **Not built.** The band is measured; the second arm is not run, so no cost-per-1,000-pairs comparison between arms is published. |
| OpenSanctions external baseline | **Impossible.** The dataset no longer exists — see above. |

**Built, because `SKILL_MATRIX.md` makes project 5 the sole home of it:** expand/contract migration
against fixed legacy schemas, the additive merge ledger, and a committed unmerge test. Skipping that
would empty a load-bearing row portfolio-wide, which is the mistake project 4's ADR-002 made with
rate limiting and had to be sent back to fix.
