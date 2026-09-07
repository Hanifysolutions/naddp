"""Modules of the NADDP demo seed loader.

``data/demo-seed/seed.py`` is the entry point; this package holds the dataset itself,
split by bounded context so that one file is one story. Nothing here imports FastAPI or
the AI Gateway: the seed talks to the ORM and to ``app.audit.writer`` and to nothing else.

Import order matters only in ``seed.py``: every module here is a pure function of a
:class:`~seed_parts.context.SeedContext` and the modules that ran before it.
"""

from __future__ import annotations
