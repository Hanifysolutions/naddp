"""Unified Outcomes: one governed picture, each figure counted in its own bounded context.

The picture is unified; the permissions are not. One module per context owns its figures and its
step on the hero thread, checks its own permission before it touches the session, and imports no
model from any other context. :mod:`app.services.outcomes.board` only lays their answers side by
side and never queries.
"""

from app.services.outcomes.board import OutcomesBoard, build_outcomes_board

__all__ = ["OutcomesBoard", "build_outcomes_board"]
