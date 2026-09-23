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

**Every count is computed from the committed artifacts by `tests/test_kill_criteria.py`, not
asserted in prose.** (Earlier drafts of this file named a `scripts/gate.py`; no such file was
ever written, and the work is done by the test suite.)

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

---

## ADR-002 — What the development corpus changed, written down before the hold-out is scored

**Status:** accepted, 2026-09-24, **after development scoring and before the hold-out is scored.**

This record exists so the order is checkable in git rather than asserted. Everything below was
decided from the **development** corpus. The hold-out has never been scored at the time this file is
committed; `artifacts/evaluation.json` at this commit contains no `holdout` key, which is the
mechanical form of that claim.

### The hold-out's negatives were re-drawn. Its entities were not.

`bb02786` committed a corpus whose negatives were taken in blocking-iteration order. That did not
implement ADR-001's requirement that negatives be "hard by construction", and it showed: every arm,
the system and all three baselines, scored ~1.0 precision. A corpus on which nothing can be wrong
measures nothing — `artifacts/evaluation-first-construction.json` is that run, kept so the claim is
readable rather than recalled: 1.000, 0.9997, 1.000 and 1.000 precision across the four arms.
Negatives are now the most name-similar unlinked pairs available, drawn within one side of the split
against a per-side quota.

Re-drawing negatives changes which negative pairs land in the hold-out. It does not change **which
entities** are held out, and that is the property the split exists to protect. Verified against the
`bb02786` artifact, not asserted:

| | |
|---|---|
| cluster-to-side assignment | identical |
| held-out positives, then and now | 1,226 of 1,226 — the same pairs |
| pairs that crossed between development and hold-out | 0 |
| hold-out pairs that changed | 1,145, **all negatives**, all drawn within the hold-out side |

### The weighted score does not assert a match

`THRESHOLD_MATCH` is `None`. Measured, not preferred: a MATCH band at 0.86 decided 234 development
pairs, 151 correctly and 83 wrongly — **0.645 precision inside the band**. ADR-001 had already fixed
that a wrong merge is the expensive error, and a rule that is wrong a third of the time is not an
auto-merge rule, it is a queue. Removing the band costs 0.029 recall and removes 83 of 89 false
merges. The whole sweep is published in `artifacts/evaluation.json` under `operating_points`.

So a MATCH comes from a fact — a registrar's number, or exact agreement of the whole canonicalised
name — and the weighted score decides only whether the rest is worth a person's time.

**`THRESHOLD_NO_MATCH` cannot be tuned toward the kill test**, and the sweep proves it rather than
claiming it: precision, recall and F1 are identical down the whole REVIEW column, because that
cut-off only moves pairs between REVIEW and NO_MATCH and ADR-001 charges those identically. It is
chosen operationally instead — the widest review band the predeclared 25% abstention cap permits,
which puts the most real duplicates in front of a person. At 0.66 the queue is 21.2% of pairs and
36.4% of it is genuine duplicates.

### A rule that looked obviously right, and the corpus said no

A `legal_form_conflict` hard signal — disjoint canonical legal forms mean different legal entities —
was added on the strength of `Cheyne ... L.P.` against `Cheyne ... Inc.`, and then measured: **152
true negatives against 76 false negatives.** The false negatives are `Scollard Energy Inc.` against
`Scollard Energy Ltd.`, `balandis real estate ag` against `balandis real estate GmbH` — adjudicated
duplicates, because conversion and re-domiciliation are things companies do. The corpus labels 151
positives `legal_form_variance` outright. The rule was removed. It is recorded here because "we tried
the obvious thing and the data refused it" is the part usually left out.

### Three rules that survived, with what each one costs

Measured per signal on development, so a reader can price them rather than trust them:

| signal | TP | FP | FN | TN |
|---|---|---|---|---|
| identifier agreement | 1,893 | 0 | — | — |
| identifying-name agreement | 1,139 | 6 | — | — |
| identifier conflict | — | — | 405 | 1,144 |
| discriminator conflict | — | — | 6 | 200 |

**Identifier agreement is only a fact when the identifier identifies.** Every Allianz fund at
RA000665 carries the same `registeredAs`, so the rule asserted that a small-cap equity fund and a
bond fund were one company — 40 false merges. Agreement now requires the number to appear on at most
two records corpus-wide, which is what a duplicate and its successor look like.

**A registrar's number outranks a name, including an identical one.** Exact name agreement used to be
checked first, and two SEC series — `S000016688` and `S000015881`, both named `High Yield Strategy
Fund` — were merged because the strings matched. Ordering the checks is the whole design.

**Identifier conflict costs 405 adjudicated duplicates and this is not fixable here.** `ALUDIUM ...
S.L.` appears under `BI-66019` and `1000420033544` at one authority and is one company; `High Yield
Strategy Fund` appears under two SEC series numbers and is two funds. The two situations are
structurally identical in the evidence available, so no rule over this data separates them. The
conflict rule keeps the 1,144 and pays the 405, because it produces zero false merges.

**The discriminator rule now requires a designator on both sides.** `Target 2027` against
`Target 2037` is two products; `NB Holdings Corporation (7690)` against `NB Holdings Corporation` is
one company written down twice, and so is `Gladiator Equities P/L` against `Gladiator Equities Pty
Ltd`, where `P/L` splits into two single letters that look like designators. Recovered 20 adjudicated
duplicates at no cost in false merges.

### Two bugs the guards found, both of which had been silently deflating the evidence

**The legal-form table was mostly unreachable.** Canonicalisation looked up one token at a time, so
every multi-word entry — `spolka z ograniczona odpowiedzialnoscia`, `gesellschaft mit beschraenkter
haftung`, `company limited` — could never match, and two whole groups had multi-token canonical
*values* that nothing downstream could strip. `features.py` carried a comment asserting that `Sp.K`
and `SPOLKA KOMANDYTOWA` became one token; they did not. Canonicalisation now matches longest-first
over n-grams, so the table means what it says, and `lp` was added — its absence was why
`Cheyne ... L.P.` and `Cheyne ... Inc.` looked alike.

**"Casing drift" could not detect casing.** `differs_only_by_diacritics_or_case` asked
`normalize_name(..., fold=False)` whether two names differed, but normalisation lowercases whether or
not it folds accents — so `DYNAMIC FIXED INCOME FUND` and `Dynamic Fixed Income Fund` came back
identical and the variant named in the function's own title was invisible. It under-counted that
drift **19-fold: 39 pairs became 740.** Kill test B failed and is what caught it. The `fold`
parameter bought nothing it claimed to and was removed.

### The honest reading of kill test F

F passes on development: system precision **0.9980** against the best baseline's **0.9973**. That
margin is **one false merge** — six against seven — and nobody should read it as a comfortable win.

Two things must be said beside it, because the table says them anyway:

- **`identifier_first` beats the system on F1**, 0.7607 against 0.7303. F was predeclared on
  precision and the system wins there, decisively on count — 6 false merges against 48. It does not
  win on the aggregate, and this document is not going to quote only the metric that flatters it.
- **The system's recall comes almost entirely from hard rules.** The weighted-feature apparatus
  earns its place as a triage queue, not as a matcher. That is a smaller claim than the architecture
  implies, and it is the one the measurement supports.

### The ceiling nothing here can lift: the negative class contains unadjudicated duplicates

Negatives are the most name-similar pairs GLEIF has *not* linked, which selects precisely for
duplicates nobody has adjudicated yet. `TIFFANY AND COMPANY` at RA000628 against `TIFFANY & CO.` at
RA000602 is labelled a negative and is almost certainly one company. Every arm is charged for these,
and arms that decide more are charged more. Published precision is therefore a **floor**, not an
estimate, and no number in this repository should be read as the true one.

---

## ADR-003 — The hold-out, scored once

**Status:** accepted, 2026-09-24, **after** `549ab39`, which is the commit that contains ADR-002, the
corpus, the frozen split and a development-only `evaluation.json` with no `holdout` key. Everything
here was measured after that commit and **nothing in the decision path changes on the basis of it.**

### The result

| arm | precision | recall | F1 | false merges |
|---|---|---|---|---|
| `exact_normalized_name` | 0.9967 | 0.4902 | 0.6572 | 2 |
| `fuzzy_name_only_0.90` | 0.8890 | 0.6468 | 0.7488 | 99 |
| `identifier_first` | 0.9817 | 0.6117 | 0.7538 | 14 |
| **system** | **0.9986** | 0.5808 | 0.7344 | **1** |

2,452 pairs, 1,226 positive and 1,226 negative, over entity clusters no development pair touches.

### It generalises, and that is the whole point of the file

| | development | hold-out | gap |
|---|---|---|---|
| precision | 0.9980 | 0.9986 | +0.0006 |
| recall | 0.5758 | 0.5808 | +0.0050 |
| F1 | 0.7303 | 0.7344 | +0.0041 |

Project 3 published a development F1 of 0.963 and a held-out F1 of 0.328, and that gap is the reason
`holdout.py` exists. Here the two sides agree to within 0.005 on every metric. The reason is not
skill: **there is no fitted parameter to overfit.** The decision is deterministic rules over
interpretable features, the weights were argued from meaning rather than tuned, and the one number
chosen from the development curve — `THRESHOLD_MATCH` — was chosen by removing the band entirely.
A system with nothing to fit generalises; the hold-out is what turns that from an argument into a
measurement.

### The hold-out's negatives are easier, and the precision must be read with that in mind

Hard-negative mining ranks candidates by name similarity and keeps the most confusable, against a
per-side quota. The development side has roughly four times the records, so its quota reaches far
deeper into a far larger pool of near-misses:

| | negatives | mean name similarity | median | share ≥ 0.90 |
|---|---|---|---|---|
| development | 5,266 | 0.9037 | 0.8923 | 43.2% |
| hold-out | 1,226 | 0.8147 | 0.8266 | 8.1% |

`fuzzy_name_only_0.90` shows the size of that difference plainly: 0.5936 precision on development
against 0.8890 on the hold-out, from the same rule. So **the hold-out's absolute precision is not
comparable to development's**, and the honest claim is the relative one — the same ordering of arms,
and the same distance between them, on entities the rules have never seen. The development number is
the conservative one and is the number this project quotes.

The review rate moves for the same reason: 21.2% against 11.6%. A queue is only as large as the
number of genuinely ambiguous pairs put in front of it.

### What did not change

No threshold, feature, weight, hard signal, normalisation rule or blocking key was altered after the
table above existed. ADR-001 fixed that, and the check is `git log` **per decision-path file**:

```
git log -1 --format=%h -- src/counterparty_resolver/normalize.py   # 549ab39
git log -1 --format=%h -- src/counterparty_resolver/blocking.py    # bb02786
git log -1 --format=%h -- src/counterparty_resolver/features.py    # 549ab39
git log -1 --format=%h -- src/counterparty_resolver/resolve.py     # 549ab39
```

An earlier version of this sentence said "the last commit touching `src/`", which is false: `store/`
and `api/` were both written afterwards. See ADR-004.

---

## ADR-004 — What two independent reviews found, and what changed

**Status:** accepted, 2026-09-24, after the hold-out was scored at `b67b83e`.

Two read-only reviews were run against the repository at `2345e65`: one from a hiring engineer's
perspective, one a security and correctness pass. Between them they found four false claims, two
correctness bugs in the append-only ledger and its migrations, and three undisclosed properties of
the corpus. This record exists because the findings are more useful published than fixed quietly —
several of them are exactly the failure this repository spends a whole script guarding against, made
one level up.

**Nothing below changed a decision rule.** `normalize.py`, `blocking.py`, `features.py` and
`resolve.py` are untouched since `549ab39`, which precedes the hold-out score, and every number in
the development and hold-out tables is unchanged except candidate recall — which was measuring the
wrong quantity, and is now measuring the right one.

### Four claims that did not survive checking

**"The last commit touching `src/` is `549ab39`."** False. Three later commits touch `src/`, all of
them in `store/` and `api/`. The substantive claim holds and is now stated in the form that is
actually true: the last commit touching the **decision path** — `normalize`, `blocking`, `features`,
`resolve` — is `549ab39`. `git log -1 -- <file>` checks it per file, which is what the sentence now
says.

**"Candidate recall 0.9223."** Measured by asking whether two records share a blocking key.
`blocking.py` skips any block over `MAX_BLOCK_SIZE`, so **45 adjudicated duplicates shared a key and
were never generated** and were being counted as surfaced. The honest figure is **0.9138**, it still
clears the predeclared 0.90 floor, and both numbers are now published — the stricter one as the
headline, the looser one beside it as `recall_if_block_size_were_unbounded`.

**"An unmerge restores the exact prior state."** True only from a clean slate. See below.

**"Three adversarial cases ship failing."** They do not fail: `expect` records the resolver's current
output, so the suite is green on all eighteen. The policy is unchanged and correct — a rule change
against a spent hold-out would invalidate every number here — but the cases are **published as
characterised misses**, which is what they are, and the README no longer says the suite is red.

### Two real bugs in the part of this project that is its own contribution

**An unmerge deleted a displaced link instead of restoring it.** Merge A with B, then merge A with C,
then reverse the second: A ended up under no entity at all rather than back with B. The merge that
moved A had nowhere to record where A had come from, and the chain guard only refuses reversing an
*earlier* entry, so the newest reversal in a chain passed it and destroyed the link. Migration 5 adds
`displaced_links_json`, `merge` records what it displaced and `unmerge` puts it back. The old test
compared full-table snapshots and still passed, because it only ever exercised a virgin database —
a snapshot comparison is only as strong as the state it starts from.

**A failed migration could destroy the append-only trigger permanently.** Migration 3 drops the
trigger, backfills, and recreates it, and the comment beside it claimed this was safe because it ran
in one transaction. It did not: `executescript` issues a COMMIT before it runs and lets each
statement autocommit, and `with connection:` does not change that. A failure — or a process kill
during a rolling restart, which is exactly when this happens — left the ledger **permanently
mutable**, `schema_version` still reading 2, and the migration unable to re-run because the trigger
it drops was gone. Every DDL in the package is now a tuple of statements executed inside an explicit
`BEGIN IMMEDIATE`, and the version row is written in the same transaction.

**And the transition phase was never implemented.** `migrations.py` described a middle step where
"new code writes both columns". No code did: `merge` named a fixed column list, `approved_by` is
`NOT NULL`, and every merge at migration 2 or 3 failed with a constraint error. The window the
rename exists to remove was the only window in which the shipped writer could not write. The writer
now reads `PRAGMA table_info` and fills whatever the table has, and the test — which previously
asserted two column *names* existed and then wrote through the *old* shape — now calls `merge` and
`unmerge` at every step.

### Three properties of the corpus that were true and undisclosed

**Every record participates in an adjudicated duplicate.** Negatives are mined from the same record
pool the positives built, which is GLEIF's `DUPLICATE` records and their successors. The resolver is
never scored against an ordinary counterparty record, and the reduction ratio is measured over a
pool already filtered to duplicates — so it is not the ratio the same blocking would achieve over a
counterparty master. Now stated in the README, in `DATA_PROVENANCE.md`, and in the artifact itself.

**The fuzzy baseline is selected against by the corpus that scores it.** Hard negatives are ranked by
Jaro-Winkler over normalised names, which is precisely `fuzzy_name_only_0.90`'s decision function.
The miner is that baseline's adversary by construction, and its 0.5936 development precision against
0.8890 on the hold-out is that showing. Kill test F turns on `identifier_first`, which the mining
metric does not touch, so the criterion is unaffected — but that row is not a fair measurement and is
now labelled as one.

**The corpus-wide frequency tables see the hold-out.** `distinctive_token_agreement` and the
identifier-distinctiveness guard read document-frequency tables computed over every record in the
corpus, held-out records included. No label is involved, so this is not label leakage — but it is
information from the held-out records reaching their own scoring, and "there is no fitted parameter
to overfit" was too strong. It is now **priced rather than argued about**:

| | development precision | hold-out precision | hold-out false merges |
|---|---|---|---|
| corpus-wide priors (shipped) | 0.9980 | 0.9986 | 1 |
| development-only priors | 0.9977 | 0.9972 | 2 |

Both arms are computed by `scripts/evaluate.py` and published under `prior_sensitivity`. The
conservative arm is the floor, and kill test F passes under either.

### Security and correctness, smaller but real

- **The ledger recorded an identity the caller chose.** `approver_id` was read from an
  `x-approver-id` request header, so the append-only audit trail attributed merges to whatever the
  client said. One shared token means one identity; it now records that, and nothing else.
- **A non-ASCII token returned 500 instead of 401**, because `hmac.compare_digest` refuses non-ASCII
  `str`. Compared as bytes.
- **`CR_DATABASE` pointed at a file crash-looped after the first boot**, because seeding was
  unconditional. `render.yaml` and `.env.example` both invite that configuration.
- **The legacy-table guard was bypassable and blind to DML.** It matched on the verb and captured
  the first word after it, so `ALTER TABLE main.legacy_gleif` slipped through and
  `DELETE FROM legacy_gleif` was never examined at all. It is now anchored on the table names: any
  statement that is not read-only and names a legacy table is refused, `CREATE VIEW` excepted
  because that is how the constraint is satisfied.
- **The breach harness ignored git's exit code**, so a git that failed for any reason would have let
  it report "the working tree is unchanged" having checked nothing. `check=True` on both calls.
- **There was no logging anywhere in `src/`.** A refused approval vanished silently, in a system
  whose own argument is that the person asking why a payment went astray needs the record.
- **`merge` was not internally idempotent**, only idempotent-given-the-console's-lock. It now
  catches the UNIQUE violation and returns the winner's entry, so the guarantee belongs to the
  module that documents it.

### Five assertions that could not fail, and the breaches added for them

`assert "contributed" in body` matched a static `<th>`; `assert "beats it on F1" in body` matched
static prose; the rename test asserted column names; a feature-contract test named three properties
and checked two; and a candidate-generation test compared `0 == 0` when nothing was generated. All
five now assert rendered data or counted values. `scripts/plant_breaches.py` grew from 16 to **23**
breaches, covering every fix above that has a guard.

### What this says about the repository

Every documentation surface in this project is a promise; every mechanism in it is enforced. The
findings cluster almost entirely on the first kind, which is the predictable place for a repository
that spends its effort on the second. The response is not more prose — it is
`tests/test_published_numbers.py`, which parses the README's result tables and fails the build when
a cell disagrees with `artifacts/evaluation.json`, and `tests/test_evaluation_method.py`, which
asserts that the quantities being compared to ADR-001's thresholds are the quantities those
thresholds name.
