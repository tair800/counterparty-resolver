# Architecture

Two things shape every box below, and both come from `DECISIONS.md` rather than from taste.

**A wrong merge is the expensive error.** A missed duplicate costs a duplicate; a bad merge corrupts
payment routing and takes days to unpick. So the decision has three answers, facts outrank
similarity, and a person approves every merge.

**The source schemas are fixed.** Everything this project owns is additive — views, a ledger, a
crosswalk. A migration that names a legacy table is refused before it runs.

---

## The pipeline

```
 ┌──────────────────────┐      ┌──────────────────────────────┐
 │  legacy_gleif        │      │  legacy_companies_house      │   FIXED. Never altered.
 │  prior names:        │      │  prior names:                │   A migration naming one
 │  a typed array       │      │  ten numbered columns        │   is refused by migrate().
 └──────────┬───────────┘      └──────────────┬───────────────┘
            │                                 │
            └──────────────┬──────────────────┘
                           ▼
              ┌─────────────────────────────┐
              │  crosswalk views (additive) │  counterparty_record · counterparty_other_name
              │  join: RA000585 + regid     │  crosswalk
              └──────────────┬──────────────┘
                             ▼
              ┌─────────────────────────────┐
              │  normalize                  │  legal forms canonicalised (multi-token),
              │                             │  accents folded, identifiers keep leading zeros
              └──────────────┬──────────────┘
                             ▼
              ┌─────────────────────────────┐
              │  blocking  (5 key families) │  nameprefix · acronym · regid · token · postal
              │  MAX_BLOCK_SIZE = 200       │  recall 0.9138 · reduction 0.995521
              └──────────────┬──────────────┘   ── caps everything downstream ──
                             ▼
              ┌─────────────────────────────┐
              │  hard signals   (facts)     │  1. identifier agreement / conflict
              │  short-circuit the sum      │  2. discriminator conflict
              │                             │  3. exact canonical-name agreement
              └──────────────┬──────────────┘
                             │ no fact to read
                             ▼
              ┌─────────────────────────────┐
              │  7 weighted features        │  each returns a value in [0,1] and a sentence
              │  score = Σ value × weight   │
              └──────────────┬──────────────┘
                             ▼
              ┌─────────────────────────────┐
              │  bands                      │  score ≥ 0.66 → REVIEW,  else NO_MATCH
              │  THRESHOLD_MATCH = None     │  the score never asserts MATCH (ADR-002)
              └──────────────┬──────────────┘
                             ▼
              ┌─────────────────────────────┐
              │  steward console            │  4 screens · read-only without an approver token
              └──────────────┬──────────────┘
                             ▼
              ┌─────────────────────────────┐
              │  merge_ledger (append-only) │  triggers refuse UPDATE and DELETE
              │  source_link                │  unmerge restores this table exactly
              └─────────────────────────────┘
```

## Why the order of the hard signals is the design

A weighted sum is the right shape for *similarity* — names are alike, addresses are alike. It is the
wrong shape for *facts*. Two records asserting different registration numbers at the same authority
are not "somewhat similar"; one of them is about a different company, and no quantity of name
similarity may outvote that.

So facts are read first, and among the facts the registrar comes before the strings:

| | signal | decides | development TP / FP | development FN / TN |
|---|---|---|---|---|
| 1 | identifier agreement | MATCH | 1,893 / 0 | — |
| 2 | identifier conflict | NO_MATCH | — | 405 / 1,144 |
| 3 | discriminator conflict | NO_MATCH | — | 6 / 200 |
| 4 | identifying-name agreement | MATCH | 1,139 / 6 | — |
| 5 | the weighted score | REVIEW or NO_MATCH | 0 / 0 | — |

(4) sat above (1) in an earlier version and it was wrong: two SEC series, `S000016688` and
`S000015881`, are both named `High Yield Strategy Fund`, and the resolver merged them because the
strings matched.

(2) costs 405 adjudicated duplicates and that is not fixable on this evidence. `ALUDIUM … S.L.`
appears under two numbers at one authority and is one company; `High Yield Strategy Fund` appears
under two SEC series numbers and is two funds. The two situations are structurally identical in the
fields available, so no rule over this data separates them. The conflict rule keeps the 1,144 and
pays the 405, because it produces zero false merges.

## Component boundaries

| module | owns | deliberately does not |
|---|---|---|
| `normalize` | canonical form of a name or an identifier | decide anything |
| `blocking` | which pairs are ever compared | score them |
| `features` | seven similarity features **and** the hard signals, which are not features | know about thresholds |
| `resolve` | the order facts are read in, and the bands | know where records came from |
| `evaluate` | precision/recall with abstention priced, three baselines, the sweeps | touch the resolver's internals |
| `corpus` | acquisition, hard-negative mining, the group-aware split | author a positive label |
| `store` | two fixed schemas, migrations, crosswalk views, the merge ledger | decide a merge |
| `api` | four screens and the write gate | decide anything at all |

`resolve` takes `frequency`, `total_records` and `identifier_frequency` as arguments rather than
reading a global. A decision is a pure function of its inputs: the same pair scored twice with the
same tables gives the same answer, and a test can supply its own.

## State, and how little of it there is

The resolver is stateless. The only mutable state in the system is the merge ledger and the source
links derived from it, which live in SQLite.

**SQLite rather than PostgreSQL** because the resolution layer is one process with one writer, the
console's database is in memory, and `PORTFOLIO_BLUEPRINT.md`'s constraint for this increment is no
Postgres where files suffice. What SQLite does *not* do here is scale writers, and nothing in the
design depends on it not needing to: the ledger's guarantees — append-only, idempotent on a unique
key, reversible — are schema-level and port to Postgres unchanged.

**No Redis.** Blocking keys are computed, not cached; the candidate set for the development split's
10,424 records is 243,336 pairs and takes seconds. A cache would be a component with an invalidation
story and no measured problem to solve.

## The evidence, and the order it was produced in

Reproducible from the repository, and the order is visible in `git log`:

| commit | what it fixed |
|---|---|
| `b620131` | the claim, the corpus contract, the baselines, the hold-out policy, the kill test — **zero source files** |
| `bb02786` | the corpus and the frozen split — **zero evaluation files** |
| `549ab39` | the decision rules and a development-only evaluation — **no `holdout` key in the artifact** |
| `b67b83e` | the hold-out, scored once |

Nothing after `549ab39` changes a rule. That is checkable rather than promised, and the check is per
file rather than over the whole tree — `store/` and `api/` were both built afterwards:

```
git log -1 --format=%h -- src/counterparty_resolver/normalize.py   # 549ab39
git log -1 --format=%h -- src/counterparty_resolver/blocking.py    # bb02786
git log -1 --format=%h -- src/counterparty_resolver/features.py    # 549ab39
git log -1 --format=%h -- src/counterparty_resolver/resolve.py     # 549ab39
```

All four are at or before `549ab39`, and the artifact committed there has no hold-out result in it —
so no rule can have been chosen with one in hand.
