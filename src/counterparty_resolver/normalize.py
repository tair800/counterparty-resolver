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
    ("ltd", "limited", "ltd.", "co ltd", "company limited"),
    ("plc", "p.l.c.", "public limited company"),
    ("llc", "l.l.c.", "limited liability company"),
    ("llp", "l.l.p.", "limited liability partnership"),
    ("inc", "inc.", "incorporated"),
    ("corp", "corp.", "corporation"),
    ("gmbh", "g.m.b.h.", "gesellschaft mit beschraenkter haftung"),
    ("ag", "a.g.", "aktiengesellschaft"),
    ("sa", "s.a.", "societe anonyme"),
    ("sas", "s.a.s.", "societe par actions simplifiee"),
    ("sarl", "s.a.r.l.", "societe a responsabilite limitee"),
    ("bv", "b.v.", "besloten vennootschap"),
    ("nv", "n.v.", "naamloze vennootschap"),
    ("spa", "s.p.a.", "societa per azioni"),
    ("srl", "s.r.l.", "societa a responsabilita limitata"),
    ("sp k", "sp.k", "sp.k.", "spolka komandytowa", "spolka komandytowa sp k"),
    ("sp z oo", "sp. z o.o.", "sp z o o", "spolka z ograniczona odpowiedzialnoscia"),
    ("as", "a.s.", "aktieselskab", "aksjeselskap"),
    ("ab", "a.b.", "aktiebolag"),
    ("oy", "o.y.", "osakeyhtio"),
    ("pty", "pty.", "proprietary"),
    ("pvt", "pvt.", "private"),
    ("pte", "pte.", "private limited"),
    ("kk", "k.k.", "kabushiki kaisha"),
    ("bhd", "berhad"),
    ("sdn", "sendirian"),
)

#: Spelling -> canonical form. Built from the groups so the two cannot drift apart.
LEGAL_FORMS: dict[str, str] = {
    spelling: group[0] for group in _LEGAL_FORM_GROUPS for spelling in group
}

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


def normalize_name(name: str, *, fold: bool = True) -> str:
    """A company name reduced to comparable form, with legal forms canonicalised, not deleted.

    Order matters and is fixed: decompose, fold, lowercase, expand ``&``, drop noise punctuation,
    split on separators, canonicalise any legal-form token, rejoin. Folding before lowercasing means
    a name is not sensitive to whether the source stored `Ö` or `O` + combining diaeresis.

    Args:
        name: The raw name from a source system.
        fold: Whether to strip accents. False keeps them, for the feature that wants to know whether
            two names differ *only* by diacritics.
    """
    text = unicodedata.normalize("NFKC", name)
    if fold:
        text = fold_accents(text)
    text = text.lower()
    text = text.replace("&", " and ")
    text = _DROPPED.sub("", text)
    parts = [p for p in _SEPARATORS.split(text) if p]

    canonical = [LEGAL_FORMS.get(part, part) for part in parts]
    return _WHITESPACE.sub(" ", " ".join(canonical)).strip()


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
