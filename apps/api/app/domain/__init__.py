"""Pure domain layer: enums, value objects and workflow state machines.

No I/O, no SQLAlchemy, no FastAPI — this layer must stay importable in isolation.
One module per bounded context (e.g. ``opportunities.py``, ``consular.py``).
"""
