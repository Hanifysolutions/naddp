"""The deterministic fallback harness (ADR-0002) -- the thing that makes the demo un-killable.

``BUILD_BIBLE.md`` section 0: *the demo must be physically incapable of dead-ending in front
of the Ambassador*. This module is where that promise is kept. It owns three things:

1. :class:`FallbackReason` -- the closed vocabulary written to ``ai_traces.fallback_reason``.
2. The snapshot store: ``data/demo-seed/ai_snapshots/{purpose}_{scenario}.json``, loaded
   through an invalidatable cache, with a malformed file treated as a **miss** rather than
   as a crash.
3. :func:`call_with_budget` -- the hard wall-clock budget around a synchronous provider
   call.

**Why a malformed snapshot is a miss and not an error.** A JSON typo in a demo asset must
degrade to the ``__default__`` snapshot, and from there to a truthful BLOCKED envelope. It
must never be the reason a screen shows a stack trace. The failure is logged at ``error``
level with the path, so it is loud in the terminal and invisible on stage -- which is the
correct place for each.

**Why the cache caches misses too.** A miss costs one ``stat`` and one read attempt; caching
it keeps a purpose with no snapshot from re-probing the filesystem on every request during a
rehearsal. Because a miss is cached, editing or adding a snapshot file mid-process needs
:func:`clear_snapshot_cache` -- which is exactly why the cache is invalidatable, and why
``make demo-reset`` must call it.

**Why the timeout is a thread and not a signal.** ``signal.alarm`` is Unix-only and
main-thread-only; the API serves synchronous handlers on Starlette's worker threads, so it
would silently do nothing. A ``ThreadPoolExecutor`` future with ``.result(timeout=...)``
works everywhere, including Windows, which is where this is being built. The documented
cost: **the worker thread may outlive the timeout.** ``Future.result`` stops *waiting*; it
cannot stop the HTTP request the SDK is making. That is acceptable here and would not be in
general, because (a) the abandoned thread holds no lock and no transaction -- it does no
database work at all, (b) the executor is bounded, so abandoned threads cannot accumulate
without limit, and (c) the alternative, blocking the response until the provider gives up,
is precisely the fifteen-second spinner ADR-0002 exists to prevent.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from pydantic import ValidationError

from app.ai.purposes import DEFAULT_SCENARIO, PurposeSpec
from app.ai.schemas import GroundedResult
from app.core.config import SNAPSHOT_DIR, snapshot_path
from app.core.logging import get_logger
from app.domain.enums import AiPurpose, ApprovalStatus
from app.models.ai import FALLBACK_REASONS

__all__ = [
    "MAX_INFLIGHT_CALLS",
    "SNAPSHOT_SUFFIX",
    "BudgetExpiredError",
    "FallbackReason",
    "Snapshot",
    "call_with_budget",
    "clear_snapshot_cache",
    "describe_snapshot_gap",
    "load_snapshot",
    "resolve_snapshot",
    "shutdown_executor",
    "snapshot_file",
    "snapshot_inventory",
    "snapshot_key",
]

_logger = get_logger(__name__)

SNAPSHOT_SUFFIX: Final[str] = ".json"

#: Ceiling on concurrent abandoned provider calls. Small on purpose: a demo makes about a
#: dozen AI calls in ten minutes, and an unbounded executor would turn a provider outage
#: into unbounded thread growth.
MAX_INFLIGHT_CALLS: Final[int] = 8


class FallbackReason(str, Enum):  # noqa: UP042
    """Why a deterministic snapshot was served instead of a live answer.

    ``(str, Enum)`` matching the locked shape of ``app/domain/enums.py``.

    **This resolves a live conflict rather than inventing a third list.** ADR-0002's
    failure-mode table and ``app.models.ai.FALLBACK_REASONS`` name the same seven modes with
    five different spellings (``PROVIDER_ERROR`` vs ``API_ERROR``, ``RATE_LIMITED`` vs
    ``RATE_LIMIT``, ``NO_CREDENTIAL`` vs ``NO_API_KEY``, ``DEMO_MODE`` vs ``LIVE_DISABLED``,
    ``CITATION_INVALID`` vs ``CITATION_CHECK_FAILED``). The column wins, because the column
    is what a query filters on and what the trace drawer renders, and because the model
    module explicitly asks the Gateway to "validate against [it] rather than writing the
    literals at the call site". The equality is asserted at import below, so the two can
    never drift apart silently.
    """

    TIMEOUT = "TIMEOUT"
    API_ERROR = "API_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    NO_API_KEY = "NO_API_KEY"
    LIVE_DISABLED = "LIVE_DISABLED"
    CITATION_CHECK_FAILED = "CITATION_CHECK_FAILED"


_DECLARED: Final[frozenset[str]] = frozenset(reason.value for reason in FallbackReason)
if frozenset(FALLBACK_REASONS) != _DECLARED:  # pragma: no cover - import-time guard
    _msg = (
        "FallbackReason and app.models.ai.FALLBACK_REASONS disagree: "
        f"{sorted(_DECLARED ^ frozenset(FALLBACK_REASONS))}. The trace column and the "
        "Gateway must use one vocabulary or ai_traces.fallback_reason becomes unqueryable."
    )
    raise RuntimeError(_msg)


class BudgetExpiredError(TimeoutError):
    """The provider call exceeded ``AI_GATEWAY_TIMEOUT_SECONDS`` and was abandoned."""


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A validated deterministic answer, ready to be wrapped in an envelope.

    ``evidence_ids`` rather than fully-rendered evidence objects is a deliberate deviation
    from ADR-0002's "serialised GatewayResult payload". The ADR's shape duplicates every
    citation title and URL into fourteen snapshot files, where they rot silently against
    ``citations.json``. Storing ids and hydrating them through
    :func:`~app.ai.evidence.evidence_refs_for` at serve time means a snapshot **cannot**
    display a stale title or a dead URL, and it makes "every snapshot cites only VERIFIED
    ids" a one-line test rather than a deep comparison. ``approval_status`` and ``result``
    are unchanged from the ADR.
    """

    purpose: AiPurpose
    scenario: str
    key: str
    path: Path
    result: GroundedResult
    evidence_ids: tuple[str, ...]
    approval_status: ApprovalStatus
    notes: str | None = None


def snapshot_key(purpose: AiPurpose, scenario: str) -> str:
    """Return the ``{purpose}_{scenario}`` key, lower-cased. Also the file stem."""
    return f"{purpose.value.lower()}_{scenario}"


def snapshot_file(purpose: AiPurpose, scenario: str) -> Path:
    """Return the path a snapshot for ``(purpose, scenario)`` would live at."""
    return snapshot_path(purpose.value.lower(), scenario)


_CACHE_LOCK: Final[threading.Lock] = threading.Lock()
_CACHE: dict[str, Snapshot | None] = {}


def clear_snapshot_cache() -> None:
    """Drop every cached snapshot and cached miss.

    Called by tests that write snapshot fixtures, and by ``make demo-reset``: a reset that
    left a stale snapshot in memory would serve the previous seed's answer against the new
    seed's evidence ids, which stage 8 would then refuse in front of an audience.
    """
    with _CACHE_LOCK:
        _CACHE.clear()


def _parse_snapshot(spec: PurposeSpec, scenario: str, path: Path) -> Snapshot | None:
    """Read and validate one snapshot file. Returns ``None`` for any unusable file."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        _logger.error("ai.snapshot.unreadable", path=str(path), error=str(exc))
        return None

    try:
        document: object = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        _logger.error("ai.snapshot.invalid_json", path=str(path), error=str(exc))
        return None

    if not isinstance(document, dict):
        _logger.error("ai.snapshot.not_an_object", path=str(path))
        return None

    declared = document.get("purpose")
    if declared != spec.purpose.value:
        _logger.error(
            "ai.snapshot.purpose_mismatch",
            path=str(path),
            declared=declared,
            expected=spec.purpose.value,
        )
        return None

    raw_result = document.get("result")
    if not isinstance(raw_result, dict):
        _logger.error("ai.snapshot.missing_result", path=str(path))
        return None

    try:
        result = spec.output_schema.model_validate(raw_result)
    except ValidationError as exc:
        _logger.error("ai.snapshot.schema_invalid", path=str(path), error=exc.error_count())
        return None

    raw_ids = document.get("evidence_ids")
    if not isinstance(raw_ids, list) or not all(isinstance(item, str) for item in raw_ids):
        _logger.error("ai.snapshot.evidence_ids_invalid", path=str(path))
        return None

    raw_status = document.get("approval_status", spec.approval_status.value)
    try:
        approval_status = ApprovalStatus(raw_status)
    except ValueError:
        _logger.error("ai.snapshot.approval_status_invalid", path=str(path), value=raw_status)
        return None

    notes = document.get("notes")
    return Snapshot(
        purpose=spec.purpose,
        scenario=scenario,
        key=snapshot_key(spec.purpose, scenario),
        path=path,
        result=result,
        evidence_ids=tuple(str(item) for item in raw_ids),
        approval_status=approval_status,
        notes=notes if isinstance(notes, str) else None,
    )


def load_snapshot(spec: PurposeSpec, scenario: str) -> Snapshot | None:
    """Return the snapshot for exactly ``(purpose, scenario)``, or ``None``.

    No ``__default__`` walk here -- :func:`resolve_snapshot` owns that -- so a caller can
    ask whether a *specific* snapshot exists, which is what the coverage test needs.
    """
    key = snapshot_key(spec.purpose, scenario)
    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]

    snapshot = _parse_snapshot(spec, scenario, snapshot_file(spec.purpose, scenario))

    with _CACHE_LOCK:
        _CACHE[key] = snapshot
    if snapshot is None:
        _logger.info("ai.snapshot.miss", purpose=spec.purpose.value, scenario=scenario)
    return snapshot


def resolve_snapshot(spec: PurposeSpec, scenario: str) -> Snapshot | None:
    """Return the snapshot for ``scenario``, else the purpose's ``__default__``, else ``None``.

    ADR-0002, "Missing snapshot": *"If no snapshot exists for the key, the harness serves
    the purpose's ``__default__`` scenario. If that too is missing, the endpoint returns a
    structured, non-crashing 'unavailable' envelope."* Returning ``None`` here is how this
    function says the second sentence; the Gateway builds the envelope.
    """
    snapshot = load_snapshot(spec, scenario)
    if snapshot is not None:
        return snapshot
    if scenario == DEFAULT_SCENARIO:
        return None
    fallback = load_snapshot(spec, DEFAULT_SCENARIO)
    if fallback is None:
        _logger.error(
            "ai.snapshot.no_default",
            purpose=spec.purpose.value,
            scenario=scenario,
            directory=str(SNAPSHOT_DIR),
        )
    return fallback


def snapshot_inventory() -> Mapping[str, Path]:
    """Return every snapshot file on disk, keyed by stem. For diagnostics and tests."""
    if not SNAPSHOT_DIR.is_dir():
        return MappingProxyType({})
    found = {path.stem: path for path in sorted(SNAPSHOT_DIR.glob(f"*{SNAPSHOT_SUFFIX}"))}
    return MappingProxyType(found)


# ---------------------------------------------------------------------------
# The wall-clock budget
# ---------------------------------------------------------------------------

_EXECUTOR_LOCK: Final[threading.Lock] = threading.Lock()
_EXECUTOR: ThreadPoolExecutor | None = None


def _executor() -> ThreadPoolExecutor:
    """Return the process-wide executor used for budgeted provider calls."""
    global _EXECUTOR
    executor = _EXECUTOR
    if executor is None:
        with _EXECUTOR_LOCK:
            if _EXECUTOR is None:
                _EXECUTOR = ThreadPoolExecutor(
                    max_workers=MAX_INFLIGHT_CALLS,
                    thread_name_prefix="ai-gateway",
                )
            executor = _EXECUTOR
    return executor


def call_with_budget[T](operation: Callable[[], T], *, seconds: float) -> T:
    """Run ``operation`` and give up after ``seconds`` of wall clock.

    A single attempt, then the caller falls back. There is deliberately no retry ladder
    inside the budget: ADR-0002 observes that retrying inside a four-second window "buys
    little and risks exceeding it".

    Raises:
        BudgetExpiredError: when the budget elapses. The worker thread is **abandoned, not
            killed** -- Python cannot kill a thread blocked in a socket read. See the module
            docstring for why that is acceptable here.
        Exception: anything ``operation`` raises is re-raised unchanged, so the Gateway can
            classify it (rate limit, API error, ...) rather than receiving a wrapper.
    """
    future: Future[T] = _executor().submit(operation)
    try:
        return future.result(timeout=seconds)
    except TimeoutError as exc:
        # ``concurrent.futures.TimeoutError`` IS the builtin since 3.11, so this clause also
        # catches a TimeoutError raised by ``operation`` itself. ``future.done()`` separates
        # the two exactly: a finished future means the operation raised, and misreporting
        # the provider's own timeout as our budget expiring would put the wrong
        # fallback_reason in the trace drawer.
        if future.done():
            raise
        # cancel() only helps if the task has not started; it usually has. Called anyway
        # because it costs nothing and does help under a saturated executor.
        future.cancel()
        msg = f"AI gateway budget of {seconds:.1f}s expired; the provider call was abandoned."
        raise BudgetExpiredError(msg) from exc


def shutdown_executor(*, wait: bool = False) -> None:
    """Tear the executor down. For process shutdown and for tests that count threads."""
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        executor = _EXECUTOR
        _EXECUTOR = None
    if executor is not None:
        executor.shutdown(wait=wait, cancel_futures=True)


def describe_snapshot_gap(spec: PurposeSpec, scenario: str) -> dict[str, Any]:
    """Explain a total snapshot miss, for the trace row and the BLOCKED envelope."""
    return {
        "purpose": spec.purpose.value,
        "scenario": scenario,
        "looked_for": [
            str(snapshot_file(spec.purpose, scenario)),
            str(snapshot_file(spec.purpose, DEFAULT_SCENARIO)),
        ],
        "snapshot_directory": str(SNAPSHOT_DIR),
        "known_snapshots": sorted(snapshot_inventory()),
    }
