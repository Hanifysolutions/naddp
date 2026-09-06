"""Version 1 of the NADDP HTTP API, mounted by ``app.main`` at ``/v1``.

Later bounded-context tracks add their routers here, one module per context
(``intelligence.py``, ``opportunities.py``, ``stakeholders.py``, ``meetings.py``,
``consular.py``, ``diaspora.py``, ``knowledge.py``, ``governance.py``)::

    from app.api.v1 import consular
    router.include_router(consular.router)
"""

from fastapi import APIRouter

from app.api.v1.meta import router as meta_router

router = APIRouter()
router.include_router(meta_router)

__all__ = ["router"]
