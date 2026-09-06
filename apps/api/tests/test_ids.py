"""Identifier generation tests.

These lock in ADR-0007: time-sortable ULID primary keys, and a citizen-facing
``public_ref`` that is opaque, high-entropy and shares nothing with the key.
"""

from __future__ import annotations

import re
import time
import uuid

import pytest

from app.core.ids import (
    PUBLIC_REF_ALPHABET,
    PUBLIC_REF_ENTROPY_BITS,
    PUBLIC_REF_GROUP_SIZE,
    PUBLIC_REF_TOKEN_LENGTH,
    is_public_ref,
    new_id,
    new_public_ref,
    normalise_public_ref,
)

#: Crockford base-32 omits exactly these four. ``U`` matters as much as the
#: others: ADR-0007 cites it, and an earlier implementation wrongly included it.
AMBIGUOUS_GLYPHS = frozenset("ILOU")

PUBLIC_REF_PATTERN = re.compile(r"^NADDP-[0-9A-Z]{8}-[0-9A-Z]{4}$")


def test_new_id_returns_a_uuid() -> None:
    assert isinstance(new_id(), uuid.UUID)


def test_new_id_is_unique() -> None:
    assert len({new_id() for _ in range(1000)}) == 1000


def test_new_id_is_time_sortable() -> None:
    """A ULID leads with its millisecond timestamp, so later ids sort higher.

    Ordering within a single millisecond is random by design, hence the sleep.
    """
    first = new_id()
    time.sleep(0.005)
    second = new_id()

    assert first.hex < second.hex


def test_public_ref_alphabet_has_no_ambiguous_glyphs() -> None:
    assert not AMBIGUOUS_GLYPHS & set(PUBLIC_REF_ALPHABET)


def test_public_ref_alphabet_is_full_crockford_base32() -> None:
    """32 symbols is what makes the entropy arithmetic in ADR-0007 exact."""
    assert len(PUBLIC_REF_ALPHABET) == 32
    assert len(set(PUBLIC_REF_ALPHABET)) == 32


def test_public_ref_entropy_matches_the_adr() -> None:
    """12 characters over 32 symbols is exactly the 60 bits ADR-0007 argues for."""
    assert PUBLIC_REF_TOKEN_LENGTH * 5 == PUBLIC_REF_ENTROPY_BITS


def test_new_public_ref_shape() -> None:
    ref = new_public_ref()
    namespace, head, tail = ref.split("-")

    assert namespace == "NADDP"
    assert len(head) == PUBLIC_REF_GROUP_SIZE
    assert len(head) + len(tail) == PUBLIC_REF_TOKEN_LENGTH
    assert set(head + tail) <= set(PUBLIC_REF_ALPHABET)
    assert PUBLIC_REF_PATTERN.match(ref)


def test_new_public_ref_is_not_derived_from_a_primary_key() -> None:
    """Two refs minted back to back must share no ordering or timestamp."""
    refs = {new_public_ref() for _ in range(500)}
    assert len(refs) == 500


def test_new_public_ref_is_not_monotonic() -> None:
    """A sortable public reference would leak arrival order and case volume.

    500 CSPRNG draws landing in ascending order has probability 1/500!, so a
    monotonic sequence here means the implementation regressed to deriving the
    reference from a ULID.
    """
    refs = [new_public_ref() for _ in range(500)]
    assert refs != sorted(refs)


def test_new_public_ref_carries_no_context_tag() -> None:
    """ADR-0007: the reference encodes no case type. It takes no arguments."""
    with pytest.raises(TypeError):
        new_public_ref("CS")  # type: ignore[call-arg]


def test_is_public_ref_accepts_what_we_generate() -> None:
    assert all(is_public_ref(new_public_ref()) for _ in range(100))


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "NADDP",
        "NADDP-7F3K9QX2",
        "NA-CS-7F3K9QX2",  # the superseded pre-ADR format
        "NADDP-7F3K9QX2-4M2W-EXTRA",
        "NADDP-7F3K9QX-4M2W",  # head one character short
        "NADDP-7F3K9QXI-4M2W",  # contains the excluded glyph I
        "NADDP-7F3K9QXU-4M2W",  # contains the excluded glyph U
        "OTHER-7F3K9QX2-4M2W",
    ],
)
def test_is_public_ref_rejects_malformed_input(candidate: str) -> None:
    assert not is_public_ref(candidate)


def test_normalise_public_ref_upper_cases_and_trims() -> None:
    ref = new_public_ref()
    assert normalise_public_ref(f"  {ref.lower()}  ") == ref


def test_normalise_public_ref_does_not_repair_ambiguous_glyphs() -> None:
    """Silently mapping O->0 could resolve a typo onto someone else's case."""
    assert normalise_public_ref("NADDP-OOOOOOOO-IIII") == "NADDP-OOOOOOOO-IIII"
    assert not is_public_ref("NADDP-OOOOOOOO-IIII")
