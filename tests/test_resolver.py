"""Unit tests for the parts a decision is assembled from.

The evaluation in `artifacts/evaluation.json` says how well the resolver does on 12,984 real pairs.
It does **not** say why, and a number that moves for an unknown reason is not a measurement. These
tests pin the individual behaviours the aggregate depends on, so a change that shifts precision by
0.01 shows up here as a named behaviour that changed rather than as a slightly different number.
"""

from __future__ import annotations

import pytest

from counterparty_resolver.blocking import blocking_keys, candidate_pairs, shares_a_key
from counterparty_resolver.domain import CandidatePair, CounterpartyRecord, Decision
from counterparty_resolver.features import (
    HardSignal,
    discriminator_conflict,
    distinctive_tokens,
    extract_features,
    hard_signal,
    legal_forms_of,
)
from counterparty_resolver.normalize import (
    acronym,
    normalize_identifier,
    normalize_name,
    strip_legal_form,
)
from counterparty_resolver.resolve import THRESHOLD_NO_MATCH, resolve_pair, score_pair


def record(name: str, **kwargs: object) -> CounterpartyRecord:
    """A record with one required field, because every other field is missing in real data."""
    kwargs.setdefault("source", "test")
    kwargs.setdefault("source_id", name)
    return CounterpartyRecord(legal_name=name, **kwargs)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------- normalisation


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme Company Limited", "acme ltd"),
        ("Acme Co Ltd", "acme ltd"),
        ("ACME LTD.", "acme ltd"),
        ("Siemens Aktiengesellschaft", "siemens ag"),
        ("NORDIC CAPITAL Sp. z o.o.", "nordic capital spzoo"),
        ("Brahms Spolka Komandytowa", "brahms spk"),
        ("Cheyne Fund L.P.", "cheyne fund lp"),
        ("Murowana Goślina S.A.", "murowana goslina sa"),
        ("Smith & Nephew", "smith and nephew"),
    ],
)
def test_legal_forms_canonicalise_including_the_multi_word_spellings(
    raw: str, expected: str
) -> None:
    """The multi-word entries in the table are reachable. For a long time they were not."""
    assert normalize_name(raw) == expected


def test_a_legal_form_is_canonicalised_rather_than_deleted() -> None:
    """`Phoenix Holdings Ltd` and `Phoenix Holdings GmbH` are different companies."""
    left, right = normalize_name("Phoenix Holdings Ltd"), normalize_name("Phoenix Holdings GmbH")

    assert left != right
    assert strip_legal_form(left) == strip_legal_form(right) == "phoenix holdings"


def test_legal_forms_of_reads_the_canonical_tokens() -> None:
    assert legal_forms_of("SES ENGINEERING PRIVATE LIMITED") == frozenset({"pvt", "ltd"})
    assert legal_forms_of("Just A Name") == frozenset()


def test_identifiers_keep_their_leading_zeros() -> None:
    """Companies House numbers are zero-padded and `01234567` is not `1234567` in any join."""
    assert normalize_identifier(" 01234567 ") == "01234567"
    assert normalize_identifier("") is None
    assert normalize_identifier(None) is None


def test_an_acronym_skips_the_legal_form() -> None:
    """Otherwise every English company ends in `l` and the acronym says nothing."""
    assert acronym("international business machines") == "ibm"
    assert acronym(normalize_name("Acme Global Holdings Limited")) == "agh"


# ---------------------------------------------------------------------------------- blocking


def test_blocking_surfaces_a_pair_that_agrees_on_the_registration_number() -> None:
    left = record("Wildly Different Name", registration_authority="RA000585", registered_as="0123")
    right = record("Nothing Alike At All", registration_authority="RA000585", registered_as="0123")

    assert any(key.startswith("regid:") for key in shares_a_key(left, right))


def test_blocking_does_not_pair_two_records_with_nothing_in_common() -> None:
    left = record("Aardvark Holdings", city="Oslo", postal_code="0150")
    right = record("Zeppelin Trading", city="Lima", postal_code="15001")

    assert shares_a_key(left, right) == ()


def test_every_blocking_key_is_namespaced_by_its_family() -> None:
    """The evaluation reports recall per key family, which needs the family to be readable."""
    keys = blocking_keys(
        record("Acme Ltd", registration_authority="RA000585", registered_as="1", postal_code="EC1")
    )

    assert keys and all(":" in key for key in keys)


def test_candidate_generation_yields_each_unordered_pair_once() -> None:
    records = [record(f"Acme Holdings {n} Limited") for n in range(4)]

    pairs = list(candidate_pairs(records))
    seen = {frozenset((left.key, right.key)) for left, right, _ in pairs}

    assert len(pairs) == len(seen)


# ---------------------------------------------------------------------------- the hard signals


def test_the_same_number_at_the_same_authority_is_a_match() -> None:
    left = record("Bloomberg Finance", registration_authority="RA000585", registered_as="01234567")
    right = record("Bloomberg Fin Ltd", registration_authority="RA000585", registered_as="1234567")

    signal, detail = hard_signal(left, right)

    assert signal == HardSignal.IDENTIFIER_AGREEMENT
    assert "01234567" in detail


def test_the_same_number_at_different_authorities_is_not_a_match() -> None:
    """Two registries issue the same numbers to different companies all the time."""
    left = record("Acme", registration_authority="RA000585", registered_as="01234567")
    right = record("Beta", registration_authority="RA000602", registered_as="01234567")

    assert hard_signal(left, right)[0] is None


def test_an_identifier_shared_by_many_records_is_not_evidence_of_identity() -> None:
    """Every Allianz fund carries one `registeredAs`. Agreement on it names a family, not a firm."""
    left = record("Allianz Global Equity", registration_authority="RA000665", registered_as="S1")
    right = record("Allianz Bondspezial", registration_authority="RA000665", registered_as="S1")

    assert hard_signal(left, right)[0] == HardSignal.IDENTIFIER_AGREEMENT
    assert hard_signal(left, right, identifier_frequency={"RA000665|S1": 47})[0] is None


def test_two_numbers_at_one_registrar_deny_the_merge_however_alike_the_names() -> None:
    left = record(
        "PHOENIX HOLDINGS LIMITED", registration_authority="RA000585", registered_as="00445790"
    )
    right = record(
        "PHOENIX HOLDINGS LIMITED", registration_authority="RA000585", registered_as="00445791"
    )

    signal, _ = hard_signal(left, right)

    assert signal == HardSignal.IDENTIFIER_CONFLICT
    assert resolve_pair(CandidatePair(pair_id="p", left=left, right=right)).decision is (
        Decision.NO_MATCH
    )


def test_a_registrars_number_outranks_an_identical_name() -> None:
    """Two SEC series with one name are two funds, and the strings do not get a vote."""
    left = record(
        "High Yield Strategy Fund", registration_authority="RA000665", registered_as="S000016688"
    )
    right = record(
        "High Yield Strategy Fund", registration_authority="RA000665", registered_as="S000015881"
    )

    assert hard_signal(left, right)[0] == HardSignal.IDENTIFIER_CONFLICT


def test_an_identical_canonical_name_is_a_match_when_no_registrar_disagrees() -> None:
    signal, _ = hard_signal(record("Acme Company Limited"), record("ACME CO LTD."))

    assert signal == HardSignal.IDENTIFYING_NAME_AGREEMENT


@pytest.mark.parametrize(
    ("left", "right", "fires"),
    [
        ("Target Retirement 2027 CIT", "Target Retirement 2037 CIT", True),
        ("Fineco AM Fund I", "Fineco AM Fund II", True),
        ("NB Holdings Corporation (7690)", "NB Holdings Corporation", False),
        ("Gladiator Equities P/L", "Gladiator Equities Pty Ltd", False),
        ("Acme Global Holdings", "Acme Global Trading", False),
    ],
)
def test_the_discriminator_rule_needs_a_designator_on_both_sides(
    left: str, right: str, fires: bool
) -> None:
    """A series that differs is two products; a suffix on one side is one company, written twice."""
    assert discriminator_conflict(left, right)[0] is fires


# -------------------------------------------------------------------------------- the features


def test_distinctive_tokens_drop_the_boilerplate_that_makes_long_names_look_alike() -> None:
    """`brahms` and `clandeboye` are the identity; `trust` and `schindlers` are the packaging."""
    frequency = {"schindlers": 900, "trust": 900, "brahms": 2}

    assert distinctive_tokens("Schindlers Trust Brahms", frequency, 1000) == frozenset({"brahms"})


def test_distinctive_tokens_fall_back_to_the_whole_name_without_a_frequency_table() -> None:
    """Degrading to the old behaviour beats degrading to an empty set."""
    assert distinctive_tokens("Acme Holdings Ltd", None, 0) == frozenset({"acme", "holdings"})


def test_every_feature_reports_a_value_a_weight_and_a_sentence() -> None:
    """ADR-001: evidence somebody can disagree with, not a number to compare with a cut-off."""
    contributions = extract_features(record("Acme Ltd"), record("Acme Limited"))

    assert {c.feature for c in contributions} == {
        "name_similarity",
        "identifying_name_similarity",
        "distinctive_token_agreement",
        "token_overlap",
        "acronym_match",
        "country_agreement",
        "address_similarity",
    }
    assert all(0.0 <= c.value <= 1.0 and c.detail for c in contributions)


def test_a_missing_country_is_not_a_disagreement() -> None:
    """Punishing a record for being incomplete punishes every record in the data this runs on."""
    both = extract_features(record("Acme", country="GB"), record("Acme", country="GB"))
    one = extract_features(record("Acme", country="GB"), record("Acme"))
    neither = extract_features(record("Acme", country="GB"), record("Acme", country="DE"))

    value = {c.feature: c.value for c in both}["country_agreement"]
    missing = {c.feature: c.value for c in one}["country_agreement"]
    conflict = {c.feature: c.value for c in neither}["country_agreement"]

    assert value == 1.0
    assert conflict == 0.0
    assert conflict < missing < value


def test_the_score_is_the_sum_of_what_the_features_contributed() -> None:
    """A total a reader cannot reconstruct from the parts is not evidence."""
    evidence = score_pair(record("Acme Holdings Ltd"), record("Acme Holdings Limited"))

    assert evidence.score == pytest.approx(
        sum(c.contribution for c in evidence.contributions), abs=1e-4
    )


# -------------------------------------------------------------------------------- the decision


def test_the_weighted_score_never_asserts_a_match_on_its_own() -> None:
    """ADR-002. Two names as alike as a score can make them still only reach REVIEW."""
    left, right = record("Acme Global Holdings"), record("Acme Global Holding")

    decision = resolve_pair(CandidatePair(pair_id="p", left=left, right=right))

    assert decision.decision is not Decision.MATCH
    assert decision.score >= THRESHOLD_NO_MATCH


def test_a_pair_with_nothing_in_common_is_a_no_match_rather_than_a_review() -> None:
    left, right = (
        record("Aardvark Holdings", country="NO"),
        record("Zeppelin Trading", country="PE"),
    )

    assert resolve_pair(CandidatePair(pair_id="p", left=left, right=right)).decision is (
        Decision.NO_MATCH
    )


def test_a_decision_carries_the_thresholds_it_was_made_under() -> None:
    """A decision that cannot be reproduced from what it recorded cannot be audited."""
    decision = resolve_pair(CandidatePair(pair_id="p", left=record("Acme"), right=record("Acme")))

    assert decision.threshold_match is None
    assert decision.threshold_no_match == THRESHOLD_NO_MATCH
    assert decision.evidence.hard_signal is not None
