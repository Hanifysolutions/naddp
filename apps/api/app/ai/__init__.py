"""AI Gateway package.

SECURITY BOUNDARY: ``app/ai/gateway.py`` is the only module in this repository
permitted to import the ``anthropic`` SDK. Everything else calls
``gateway.generate(...)`` and receives ``{result, evidence, trace_id, approval_status}``.
"""
