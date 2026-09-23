"""Deterministic normalisation. Every transformation here is reversible in the sense that matters:
you can read the input, read the output, and say whether the rule was right.

**The rule this module is built around: never erase a distinction that identifies a separate
company.** It is tempting to strip harder — drop all punctuation, fold every accent, remove short
tokens — because each step raises recall on the pairs you are looking at. Each step also merges
`Nordic Capital I` with `Nordic Capital II`, and that merge is the failure this project exists to
avoid. Where a choice is arguable, this module takes the conservative one and says so.

Legal forms are the one place we are aggressive, and only because the evidence says to: GLEIF's
adjudicated duplicates are full of `Sp.K` against `SPÓŁKA KOMANDYTOWA` and `LIMITED` against `LTD`.
The suffix is the noisiest token in a company name and the least identifying.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "LEGAL_FORMS",
    "acronym",
    "fold_accents",
    "normalize_identifier",
    "normalize_name",
    "strip_legal_form",
    "tokens",
]

#: Legal-form tokens, grouped so every spelling in a group normalises to the group's first member.
#:
#: Grouped rather than simply deleted. Deleting the suffix makes `X GmbH` and `X AG` identical, and
#: they are different companies; mapping them to distinct canonical forms keeps that difference
#: visible to the feature that looks for it while removing the spelling noise.
_LEGAL_FORM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("ltd", "limited", "co ltd", "company limited"),
    ("plc", "public limited company"),
    ("llc", "limited liability company"),
    ("llp", "limited liability partnership"),
    ("lp", "limited partnership"),
    ("inc", "incorporated"),
    ("corp", "corporation"),
    ("gmbh", "gesellschaft mit beschraenkter haftung"),
    ("ag", "aktiengesellschaft"),
    ("sa", "societe anonyme"),
    ("sas", "societe par actions simplifiee"),
    ("sarl", "societe a responsabilite limitee"),
    ("bv", "besloten vennootschap"),
    ("nv", "naamloze vennootschap"),
    ("spa", "societa per azioni"),
    ("srl", "societa a responsabilita limitata"),
    ("spk", "sp k", "spolka komandytowa"),
    ("spzoo", "sp z oo", "spolka z ograniczona odpowiedzialnoscia"),
    ("as", "aktieselskab", "aksjeselskap"),
    ("ab", "aktiebolag"),
    ("oy", "osakeyhtio"),
    ("pty", "proprietary"),
    ("pvt", "private"),
    ("pte",),
    ("kk", "kabushiki kaisha"),
    ("bhd", "berhad"),
    ("sdn", "sendirian"),
)

#: Spelling -> canonical form. Built from the groups so the two cannot drift apart.
LEGAL_FORMS: dict[str, str] = {
    spelling: group[0] for group in _LEGAL_FORM_GROUPS for spelling in group
}

#: The longest key, in tokens. Canonicalisation matches greedily up to this width.
_MAX_FORM_TOKENS = max(len(spelling.split()) for spelling in LEGAL_FORMS)

#: Punctuation that separates tokens rather than belonging to one.
_SEPARATORS = re.compile(r"[\s\u00a0,;:/\\|+\-_\u00b7\u2022\u2013\u2014]+")

#: Punctuation dropped outright. **`&` is deliberately absent** — it is kept and spelled `and`,
#: because `Smith & Nephew` and `Smith Nephew` should match while `A&B` and `AB` are different
#: enough to be worth a feature rather than an erasure.
_DROPPED = re.compile(r"[.\"'\u2019`()\[\]{}*#]")

_WHITESPACE = re.compile(r"\s+")


def fold_accents(text: str) -> str:
    """Strip combining marks after NFKD decomposition.

    `Murowana Goślina` becomes `Murowana Goslina`, which is one of the drift types the adjudicated
    duplicates actually contain. This loses information — Polish `ł` and `l` are different letters —
    and that loss is the point: the source systems have already lost it, inconsistently.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(name: str) -> str:
    """A company name reduced to comparable form, with legal forms canonicalised, not deleted.

    Order matters and is fixed: decompose, fold, lowercase, expand ``&``, drop noise punctuation,
    split on separators, canonicalise any legal-form token, rejoin. Folding before lowercasing means
    a name is not sensitive to whether the source stored `Ö` or `O` + combining diaeresis.

    Accents are always folded. An earlier signature let a caller keep them, for a feature that
    wanted to know whether two names differed *only* by diacritics -- but the function lowercases
    either way, so that caller could never see a casing difference and the option bought nothing it
    claimed to. `features.differs_only_by_diacritics_or_case` asks the raw strings instead.

    Args:
        name: The raw name from a source system.
    """
    text = fold_accents(unicodedata.normalize("NFKC", name)).lower()
    text = text.replace("&", " and ")
    text = _DROPPED.sub("", text)
    parts = [p for p in _SEPARATORS.split(text) if p]
    return _WHITESPACE.sub(" ", " ".join(_canonicalise_forms(parts))).strip()


def _canonicalise_forms(parts: list[str]) -> list[str]:
    """Replace legal-form spellings with one canonical token, longest spelling first.

    Multi-token, because most of the table is multi-token: `spolka z ograniczona
    odpowiedzialnoscia`, `gesellschaft mit beschraenkter haftung`, `company limited`. An earlier
    version looked each token up on its own, which meant every multi-word entry in the table was
    unreachable — the table declared an intent the code did not implement, and `features.py` carried
    a comment asserting behaviour that therefore did not exist. Matching over n-grams is what makes
    the table true.

    Longest-first so `company limited` becomes `ltd` rather than `co` followed by `ltd`.
    """
    out: list[str] = []
    index = 0
    while index < len(parts):
        for width in range(min(_MAX_FORM_TOKENS, len(parts) - index), 0, -1):
            canonical = LEGAL_FORMS.get(" ".join(parts[index : index + width]))
            if canonical is not None:
                out.append(canonical)
                index += width
                break
        else:  # pragma: no cover - the width-1 lookup always terminates the loop
            out.append(parts[index])
            index += 1
    return out


def strip_legal_form(normalized: str) -> str:
    """The name with canonical legal-form tokens removed, for comparing the identifying part.

    Used *alongside* the full name, never instead of it. `Phoenix Holdings Ltd` and `Phoenix
    Holdings GmbH` have identical stripped names and are different companies, so the feature that
    reads this one is balanced by the feature that reads the legal form itself.
    """
    kept = [t for t in normalized.split() if t not in LEGAL_FORMS.values()]
    return " ".join(kept) if kept else normalized


def tokens(normalized: str) -> frozenset[str]:
    """The token set of a normalised name, for overlap measures."""
    return frozenset(normalized.split())


def acronym(normalized: str) -> str:
    """First letters of the identifying tokens.

    `international business machines` becomes `ibm`. Legal-form tokens are excluded first, or every
    English company would end in `l` and the acronym would say nothing.
    """
    identifying = strip_legal_form(normalized).split()
    return "".join(token[0] for token in identifying if token)


def normalize_identifier(value: str | None) -> str | None:
    """A registration number reduced to comparable form: uppercase, alphanumerics only.

    **Leading zeros are kept.** Companies House `00445790` and `445790` are the same company and a
    reader will expect them to match, but stripping zeros also collapses genuinely distinct numbers
    in registries that do not zero-pad. The identifier feature handles the padded case explicitly
    instead, so the decision is visible rather than hidden in normalisation.
    """
    if value is None:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return cleaned or None
