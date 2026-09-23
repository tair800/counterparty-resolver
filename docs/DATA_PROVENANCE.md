# Data provenance and licensing

Every label in this repository came from somewhere, and this file says where, under what licence,
and what is redistributed. A label whose origin is not recorded is a label nobody can check, which
is why kill criterion C fails the build over any pair missing its provenance fields.

---

## GLEIF LEI (Level 1) — the labels

| | |
|---|---|
| Publisher | Global Legal Entity Identifier Foundation |
| Endpoint | `https://api.gleif.org/api/v1/lei-records?filter[registration.status]=DUPLICATE` |
| Licence | **CC0 1.0 Universal** — public domain dedication: redistribution and commercial use permitted, attribution not required |
| Revision pinned | `2026-09-23T08:00:00Z` (GLEIF golden-copy publish date, recorded on every pair) |
| Fetched | 2026-09-24, ~65 paged requests |
| Redistributed here | **Yes.** Derived pairs are committed in `artifacts/corpus.json`. CC0 permits it, and without the artifact the numbers cannot be checked. |

### What makes a positive label

A GLEIF record whose `registration.status` is `DUPLICATE` **and** whose
`entity.successorEntity.lei` resolves to a record we also hold. That is an LEI Issuing Organisation
— a regulated body — recording that two identifiers name one legal entity. It is a human
adjudication over real businesses, made for its own reasons, before this project existed.

**Nothing in this repository may author a positive label.** `corpus/build.py` has exactly one code
path that emits `label="match"` and it requires the adjudication above; there is no path that
promotes a similar-looking pair. `label_basis` on every pair names the fields it came from.

### What makes a negative label

A pair that **blocking surfaced** — similar enough to be considered — that **no `successorEntity`
relationship links, transitively**, and that is among the most name-similar such pairs available.

The transitive part matters: A merged into B and B merged into C makes A and C one entity, so
sampling A against C as a negative would put a true positive in the negative class and silently cap
recall. Clusters are closed with a union-find before any negative is drawn.

**Negatives are hard by construction, and this has a cost that must be stated.** Ranking candidates
by name similarity and keeping the most confusable selects precisely for duplicates nobody has
adjudicated yet. `TIFFANY AND COMPANY` at RA000628 against `TIFFANY & CO.` at RA000602 is labelled
a negative here and is almost certainly one company. Published precision is therefore a **floor**.

The first build drew negatives in blocking-iteration order, and every arm scored ~1.0 precision —
`artifacts/evaluation-first-construction.json` is that run, kept so the claim is readable rather
than recalled. A corpus on which nothing can be wrong measures nothing.

**The record pool is entirely duplicate-participating records, and that shapes two numbers.**
Negatives are mined from the same records the positives were built from, which are GLEIF's
`DUPLICATE` records and their successors. So the resolver is never scored against an ordinary
counterparty record, and the published reduction ratio is measured over a pool already filtered to
duplicates — it is not the ratio the same blocking would achieve over a real counterparty master.
Precision and recall are unaffected, because those are computed pair-by-pair over labelled pairs.

**The mining metric is one of the arms' decision function.** Candidates are ranked by Jaro-Winkler
over normalised names, which is exactly what `fuzzy_name_only_0.90` decides on. That baseline is
therefore scored by a corpus selected to defeat it, and its 0.5936 development precision against
0.8890 on the hold-out is that effect rather than a property of the rule. `identifier_first`, which
kill test F turns on, is unaffected — the ranking never looks at an identifier.

---

## Companies House Free Company Data — the second schema

| | |
|---|---|
| Publisher | UK Companies House |
| Licence | **Open Government Licence v3.0** — reuse and commercial use permitted with attribution |
| Used for | The **schema only**: `previous_name_1` … `previous_name_10`, from the CSV header `Previous Names (occurs max 10)` |
| Redistributed here | **No.** No extract is downloaded, vendored, or fetched at build time. |

The 493 MB extract is not needed to demonstrate the constraint. What the project needs is a second
*genuinely different, genuinely immutable* real-world schema to resolve across, and the column
layout is that. `store/schema.py` declares it; `store/crosswalk.py` unpivots the ten columns into
rows so both sources answer the same question.

**Attribution, as OGL v3 requires:** contains public sector information licensed under the Open
Government Licence v3.0.

The crosswalk key — GLEIF `registered_at = 'RA000585'` with a Companies House company number in
`registered_as`, on 111,717 LEI records — is a convention discovered in the data, not a declared
foreign key. Nothing guarantees it stays true, and a resolver that assumed it would break quietly.

---

## OpenSanctions Pairs — planned, and gone

`PORTFOLIO_BLUEPRINT.md` planned to anchor this project's numbers against OpenSanctions Pairs
(755,540 analyst-labelled pairs, CC BY-NC 4.0) and its published **91.33% F1** rule-based baseline.
Its non-commercial licence forbids redistribution, so the plan was to fetch it at build time by a
committed script and never vendor it.

**The dataset no longer exists.** Verified 2026-09-24:

| URL | result |
|---|---|
| `https://www.opensanctions.org/docs/pairs/` | 404 |
| `https://www.opensanctions.org/datasets/pairs/` | 404 |
| `https://www.opensanctions.org/docs/data/pairs/` | 404 |
| `https://data.opensanctions.org/contrib/pairs/pairs.json` | 404 |
| `https://data.opensanctions.org/datasets/latest/index.json` | 200 — 479 datasets, none pairs-like |

**Consequence, stated rather than absorbed:** this project's numbers are **externally labelled but
not externally comparable**. The labels are GLEIF's, which is the part the claim rests on. No
sentence in this repository may imply the stronger property.

---

## What is committed, and why

| artifact | size | why it is in git |
|---|---|---|
| `artifacts/corpus.json` | ~20 MB | The labels themselves. Without it, every number here is a claim rather than a check. CC0 permits redistribution. |
| `artifacts/holdout.json` | ~300 KB | The split, committed **before** anything was scored against it. Its value is entirely in that ordering, which `git log` shows. |
| `artifacts/evaluation.json` | ~10 KB | Every number in the README, plus the sweeps they were chosen from. |
| `artifacts/evaluation-first-construction.json` | ~7 KB | The run where every arm scored ~1.0, kept as evidence for ADR-002. |
| `artifacts/demo.json` | ~2 MB | The console's dataset, development split only. |
| `data/` | — | **Excluded.** Raw fetched payloads are cached here so re-running is cheap and nothing unreviewed reaches git. |

## Reproducing the corpus

```bash
make corpus     # ~65 requests to api.gleif.org, then hard-negative mining
make holdout    # deterministic: sha256(salt | cluster_root) < 0.20
```

The split needs no seed and no random state. Re-deriving it is how anyone checks that the committed
one was not quietly re-drawn after a disappointing score — which is the whole reason it is a hash
rather than a shuffle.

A rebuild that changes any label fails kill criterion D.
