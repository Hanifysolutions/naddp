"""Append-only audit event writer, middleware and query service.

Importing this package **arms the ORM immutability guard**. ``audit_events`` is
append-only (ADR-0004) and the database enforces that with a rule; the SQLAlchemy
``before_flush`` listener installed here is the second half, and it turns a silent
no-op UPDATE into a loud Python error naming the offending rows.

It is armed at package import rather than only at import of ``app.audit.writer``
because the guard has to cover sessions opened by code that never writes an audit
row -- a data fix, a migration helper, a scratch script. ``create_app()`` calls
:func:`~app.audit.writer.install_audit_immutability_guard` again at startup; the
function is idempotent, so arming it twice is free and neither path can be the only
one that works.
"""

from app.audit.writer import install_audit_immutability_guard

install_audit_immutability_guard()

__all__ = ["install_audit_immutability_guard"]
