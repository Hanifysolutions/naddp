"""Command-line entry points. The composition root, not a service.

Modules here may import both ``app.ai`` and ``app.services`` because they are where the
two are wired together -- which is exactly what ADR-0001 forbids a *service* from doing.
"""
