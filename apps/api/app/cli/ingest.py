"""``python -m app.cli.ingest`` -- run canonical ingestion over the seeded corpus.

THE COMPOSITION ROOT for embedding. ADR-0001 forbids ``app/services/`` from importing
``app.ai``: a service that could reach the Gateway directly is a service that could pass
its own unfiltered context. So ingestion and retrieval declare an ``EmbedFn`` port and
this module is where ``app.ai.gateway.embed`` is bound to it. That keeps the dependency
edge acyclic and keeps the Gateway the only path to an embedding provider.
"""

from __future__ import annotations

from app.ai.gateway import embed as gateway_embed
from app.core.db import session_scope
from app.core.logging import configure_logging
from app.services.ingestion import ingest_all


def main() -> int:
    configure_logging(level="WARNING")
    with session_scope() as session:
        report = ingest_all(session, gateway_embed)
    print("NADDP canonical ingestion")
    for line in report.as_lines():
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
