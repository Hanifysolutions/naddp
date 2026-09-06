"""Business logic, one module per bounded context.

Route handlers stay thin: they authorise, parse, delegate here, and serialise.
Domain rules never live in ``app/api``.
"""
