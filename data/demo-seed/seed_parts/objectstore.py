"""Writing the local object store, so that no ``object_uri`` in the database dangles.

``storage/README.md`` fixes the convention -- ``file://storage/<context>/<ulid>/<filename>``
-- and states the rule this module exists to keep: *a dangling ``object_uri`` is a seed bug
and must fail loudly in ``make seed``*. So the seed writes a real file for every URI it
records, and :func:`assert_resolves` re-reads it.

**What is stored, and what is not.** These files are **not** mirrors of the cited sources.
``data/demo-seed/README.md`` section 1 is explicit that intelligence cites URLs and does
not copy source content, and mirroring third-party PDFs into the repository would be
indefensible besides. What each file holds is the mission's own capture note: the title,
publisher, retrieval date, the registry summary, and the claims the page supports. That is
genuinely what ``documents.full_text`` means in this system, and it is why ``mime_type``
is ``text/plain`` even for a citation whose source is a PDF -- the stored object really is
text, and saying ``application/pdf`` about it would be a small lie in a column whose whole
job is to describe the bytes.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Final

from app.core.config import REPO_ROOT
from seed_parts.registry import Citation

__all__ = [
    "STORAGE_MIME_TYPE",
    "StoredObject",
    "assert_resolves",
    "capture_document",
    "store_text",
]

#: Root of the demo object store, resolved from the repository root exactly as
#: ``storage/README.md`` requires (never an absolute host path in the database).
STORAGE_ROOT: Final[Path] = REPO_ROOT / "storage"

#: Every seeded object is a text capture note. See the module docstring.
STORAGE_MIME_TYPE: Final[str] = "text/plain; charset=utf-8"

_URI_PREFIX: Final[str] = "file://storage/"


class StoredObject:
    """A file written into the object store, plus the facts the database records."""

    __slots__ = ("byte_size", "content_hash", "object_uri", "text")

    def __init__(self, object_uri: str, text: str) -> None:
        payload = text.encode("utf-8")
        self.object_uri = object_uri
        self.text = text
        self.byte_size = len(payload)
        self.content_hash = hashlib.sha256(payload).hexdigest()


def store_text(context: str, ulid: str, filename: str, text: str) -> StoredObject:
    """Write ``text`` to ``storage/<context>/<ulid>/<filename>`` and describe it.

    Idempotent: a re-run overwrites with identical bytes, so ``content_hash`` is stable
    and the second ``make seed`` changes nothing on disk.
    """
    directory = STORAGE_ROOT / context / ulid
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(text, encoding="utf-8", newline="\n")
    return StoredObject(f"{_URI_PREFIX}{context}/{ulid}/{filename}", text)


def assert_resolves(object_uri: str) -> Path:
    """Resolve ``object_uri`` against the repository root and confirm the file is there.

    Applies the two checks ``storage/README.md`` demands of every reader: the URI must be
    under ``storage/`` after resolution -- a ``..`` segment is a traversal attempt and is
    refused rather than normalised away -- and a missing object is surfaced, not swallowed.
    """
    if not object_uri.startswith(_URI_PREFIX):
        msg = f"object_uri {object_uri!r} does not start with {_URI_PREFIX!r}."
        raise ValueError(msg)
    relative = object_uri[len(_URI_PREFIX) :]
    if ".." in relative.split("/"):
        msg = f"object_uri {object_uri!r} contains a '..' segment; refusing to resolve it."
        raise ValueError(msg)
    resolved = (STORAGE_ROOT / relative).resolve()
    if not resolved.is_relative_to(STORAGE_ROOT.resolve()):
        msg = f"object_uri {object_uri!r} resolves outside {STORAGE_ROOT}."
        raise ValueError(msg)
    if not resolved.is_file():
        msg = f"object_uri {object_uri!r} does not resolve to a file ({resolved})."
        raise FileNotFoundError(msg)
    return resolved


def capture_document(citation: Citation, ulid: str, retrieved_at: datetime) -> StoredObject:
    """Write the mission's capture note for ``citation`` and return its store facts."""
    claims = "\n".join(f"  - {claim}" for claim in citation.supports_claims)
    snippet = citation.snippet or "(no snippet captured)"
    text = (
        "NADDP DEMO / SYNTHETIC CAPTURE NOTE\n"
        "===================================\n"
        "This file is the mission's capture note for a public source. It is NOT a copy of\n"
        "the source page and must never be treated as one: the citation is the URL, and\n"
        "the URL is the thing that resolves.\n\n"
        f"citation_id : {citation.id}\n"
        f"title       : {citation.title}\n"
        f"publisher   : {citation.publisher}\n"
        f"jurisdiction: {citation.jurisdiction.value}\n"
        f"source_type : {citation.source_type.value}\n"
        f"published   : {citation.published_date or 'undated'}\n"
        f"retrieved   : {retrieved_at.isoformat()}\n"
        f"verification: {citation.status}\n\n"
        "SUMMARY\n-------\n"
        f"{citation.summary}\n\n"
        "SNIPPET\n-------\n"
        f"{snippet}\n\n"
        "CLAIMS THIS PAGE SUPPORTS\n-------------------------\n"
        f"{claims}\n"
    )
    return store_text("intelligence", ulid, f"{citation.id}.txt", text)
