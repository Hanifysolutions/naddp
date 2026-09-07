"""``python -m app.ai.reset`` -- drop the Gateway's in-process caches.

Run by ``make demo-reset`` after the schema has been dropped, re-migrated and re-seeded.

**What is cached, and why a stale cache is dangerous rather than merely slow.**
``app.ai.fallback`` memoises snapshots *and misses*, and ``app.ai.evidence`` memoises the
citation registry. A reset that left either warm would serve the previous seed's answer
against the new seed's evidence ids -- and stage 8 of the pipeline checks every cited id
against the authorised set, so it would refuse the answer. In front of an audience that
looks like the Gateway is broken, when what actually happened is that a cache outlived the
data it described.

**The honest limitation, stated rather than buried.** Both caches are process-local. This
command clears them in *its own* process, which is the right thing for the seed loader, an
eval run and CI -- anything that loads snapshots in the same shell. It cannot reach into a
uvicorn worker that is already running: an API process started before the reset keeps its
warm cache until it is restarted. ``make dev`` runs uvicorn with ``--reload``, which
restarts on a code change and not on a data change, so the operator instruction is real and
is printed below rather than assumed. A ``POST /v1/admin/flush-caches`` would remove the
caveat and is recorded in ``docs/OPEN_QUESTIONS.md`` rather than invented here, because a
new privileged endpoint is the architect's decision and not a build step's.

Deliberately not a ``__main__.py`` on the package: ``python -m app.ai`` reading as "reset
the AI" is a footgun in a runbook, and a named module says what it does.
"""

from __future__ import annotations

from app.ai.evidence import reset_citation_registry
from app.ai.fallback import clear_snapshot_cache

__all__ = ["main"]


def main() -> None:
    """Clear the snapshot cache and the citation registry, and say so."""
    clear_snapshot_cache()
    reset_citation_registry()
    # This is a CLI: its stdout is the deliverable, not a stray debug print.
    print(
        "ai caches cleared (snapshots + citation registry). "
        "A uvicorn process started before this ran still holds its own: restart it."
    )


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    main()
