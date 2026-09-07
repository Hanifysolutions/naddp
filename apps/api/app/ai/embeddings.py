"""Embedding providers. Reachable ONLY through ``app.ai.gateway.embed`` (ADR-0001).

READ THIS BEFORE CHANGING ANYTHING HERE.

**Anthropic does not offer an embeddings API.** The Claude API surface is Messages,
Batches, Files, Token Counting, Models and Admin -- there is no embeddings endpoint. The
Week 2 brief asked for "Anthropic embeddings via the Gateway", which cannot be built as
written. This is ``docs/OPEN_QUESTIONS.md`` Q-07, open since Week 1 and still unanswered:
the ``vector(1536)`` dimension is locked in the schema, and 1536 is not a dimension the
Anthropic-recommended partner (Voyage) emits -- it is OpenAI's ``text-embedding-3-small``
shape. Resolving Q-07 therefore means either a second provider SDK enters the repository
or the dimension changes.

**BUILD_BIBLE section 4a makes the answer partly moot, and that is the useful part.**
``CONSULAR-SENSITIVE`` never routes to an external model: no external call at all,
metadata only, generation withheld. Embedding is a model call over the document's full
text, so *whatever* provider Q-07 selects, consular content can never be embedded by it.
A local, in-process embedder is therefore not a stopgap for the consular zone -- it is the
only compliant route, permanently.

So this module defines a provider protocol with two implementations:

* :class:`DeterministicEmbedder` -- the default and the sovereign route. Pure function of
  the text, no network, no key, no third party. Reproducible across ``make demo-reset``,
  which is what makes a retrieval demo rehearsable. Its vectors carry real lexical signal
  (hashed token features, L2-normalised) so cosine similarity is meaningful rather than
  noise, but it is emphatically NOT a semantic model: it matches on shared vocabulary, not
  on meaning. Week 2 retrieval is hybrid precisely so the lexical half carries the weight
  the vector half cannot yet.
* :class:`ExternalEmbedder` -- the seam for whatever Q-07 picks. It deliberately raises
  until a provider is wired, rather than silently degrading, and the Gateway refuses to
  route CONSULAR-SENSITIVE text to it regardless.

The routing decision between the two is the Gateway's, is recorded on every trace, and is
tested. Nothing outside ``app/ai`` may import this module.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Final, Protocol

from app.core.logging import get_logger
from app.core.text import normalise_text

__all__ = [
    "DETERMINISTIC_MODEL_ID",
    "EMBEDDING_DIM",
    "DeterministicEmbedder",
    "EmbeddingProvider",
    "EmbeddingUnavailableError",
    "ExternalEmbedder",
]

_logger = get_logger(__name__)

#: Locked by the schema (``vector(1536)`` on documents, knowledge_articles and chunks).
EMBEDDING_DIM: Final[int] = 1536

#: Recorded on every row this embedder writes. Vectors from different models are not
#: comparable, so ``document_chunks.embedding_model`` exists to make a provider change a
#: visible re-embed rather than a silent corruption of the index.
DETERMINISTIC_MODEL_ID: Final[str] = "naddp-local-hashed-tfidf-v1"

_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")

#: Words carrying no discriminative signal. Deliberately short: an aggressive stop list
#: costs recall, and the lexical half of the hybrid is doing the real work here.
_STOPWORDS: Final[frozenset[str]] = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "was",
        "were",
        "will",
        "with",
        "this",
        "these",
        "those",
        "they",
        "their",
        "there",
        "than",
        "then",
    ]
)


class EmbeddingUnavailableError(RuntimeError):
    """No embedding provider can serve this request.

    Raised rather than returning a zero vector. A zero vector is a valid input to cosine
    similarity and would quietly rank as maximally dissimilar to everything, turning a
    configuration error into a silently empty result set.
    """


class EmbeddingProvider(Protocol):
    """What the Gateway needs from an embedder."""

    @property
    def model_id(self) -> str:
        """Stable identifier recorded alongside every vector this provider produces."""
        ...

    @property
    def sends_text_off_box(self) -> bool:
        """Whether embedding transmits the text to a third party.

        The Gateway reads this to enforce BUILD_BIBLE section 4a. It is a property of the
        provider, not a flag a caller passes, so a caller cannot assert otherwise.
        """
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one unit-length vector per input, in order."""
        ...


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


@dataclass(frozen=True)
class DeterministicEmbedder:
    """Local hashed-feature embedder. No network, no key, no third party.

    The feature space is the hashing trick: each token (and each adjacent bigram, so some
    word order survives) is hashed to a dimension and accumulates a sub-linear term weight.
    The result is L2-normalised, which makes the cosine distance pgvector computes a
    genuine similarity rather than a length comparison.

    Honest about what this is: lexical overlap, not semantics. It will not match
    "processing-skills shortage" to "metallurgical workforce gap" the way a trained model
    would. That is why Week 2 retrieval is hybrid and why the Q-07 seam below exists.
    """

    dim: int = EMBEDDING_DIM

    @property
    def model_id(self) -> str:
        return DETERMINISTIC_MODEL_ID

    @property
    def sends_text_off_box(self) -> bool:
        return False

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        tokens = _tokens(normalise_text(text))
        if not tokens:
            # A chunk with no indexable tokens gets an explicit zero vector rather than an
            # exception: it is legitimately unmatchable, and the caller should still store
            # a row for it so provenance and ordinals stay contiguous.
            return [0.0] * self.dim

        features = Counter(tokens)
        features.update(f"{a}_{b}" for a, b in itertools.pairwise(tokens))

        vector = [0.0] * self.dim
        for feature, count in features.items():
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            # Sign from an independent byte so colliding features cancel as often as they
            # reinforce, instead of every collision inflating the same direction.
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


@dataclass(frozen=True)
class ExternalEmbedder:
    """Seam for the provider Q-07 selects. Raises until one is wired.

    Deliberately not implemented. Writing a speculative client for a provider nobody has
    chosen would mean either guessing an SDK surface or adding a dependency the architect
    has not approved, and the failure mode of a half-wired embedder is a silently wrong
    index rather than a loud error.

    When Q-07 is answered: implement :meth:`embed` here, keep ``sends_text_off_box`` true,
    and let the Gateway's section 4a check keep consular text away from it.
    """

    model: str

    @property
    def model_id(self) -> str:
        return self.model

    @property
    def sends_text_off_box(self) -> bool:
        return True

    def embed(self, texts: list[str]) -> list[list[float]]:
        msg = (
            "No external embedding provider is configured. Anthropic has no embeddings "
            "API, and docs/OPEN_QUESTIONS.md Q-07 (which provider supplies the "
            "1536-dimension vectors) is unresolved. The local DeterministicEmbedder is "
            "the configured default; set AI_EMBEDDING_PROVIDER only once Q-07 is answered "
            "and this class is implemented."
        )
        _logger.error("ai.embeddings.external_not_configured", model=self.model, texts=len(texts))
        raise EmbeddingUnavailableError(msg)
