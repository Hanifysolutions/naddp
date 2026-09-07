"""The AI Gateway is a security boundary, and this is the test that says so mechanically.

``CLAUDE.md`` 2.1 and ADR-0001: **application code never imports the Anthropic SDK**. Only
``app/ai/gateway.py`` may. ADR-0001's enforcement table specifies exactly this check --
walk every module under ``apps/api/app``, parse it with :mod:`ast`, and fail if any module
other than the Gateway imports ``anthropic``.

**AST rather than grep**, verbatim from the ADR, and the reason matters: grep trips over a
docstring that merely mentions the SDK (this file, and ``app/ai/__init__.py``, both do), and
grep misses ``import anthropic as sdk`` if the pattern was written for the plain form. The
parser sees the import statements and nothing else.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core.config import APP_DIR

#: The single module permitted to touch a model-provider SDK.
GATEWAY = APP_DIR / "ai" / "gateway.py"

#: Provider SDKs. ``anthropic`` today; the rule is about model access, not about one vendor,
#: so an embeddings provider arriving under Q-07 belongs on this list rather than in an
#: exemption (ADR-0001: "or any future model-provider SDK").
PROVIDER_PACKAGES = frozenset({"anthropic"})


def _modules() -> list[Path]:
    modules = sorted(APP_DIR.rglob("*.py"))
    assert modules, "no application modules found; the walk is looking in the wrong place"
    return modules


def _imported_roots(tree: ast.AST) -> set[str]:
    """Every top-level package name imported anywhere in ``tree``, aliases included."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_only_the_gateway_imports_a_provider_sdk() -> None:
    offenders: list[str] = []
    for path in _modules():
        if path == GATEWAY:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        breached = _imported_roots(tree) & PROVIDER_PACKAGES
        if breached:
            offenders.append(f"{path.relative_to(APP_DIR)} imports {sorted(breached)}")
    assert not offenders, (
        "The AI Gateway is the single boundary for model access (ADR-0001). "
        f"These modules import a provider SDK directly: {offenders}"
    )


def test_the_gateway_does_import_the_sdk() -> None:
    """The negative test alone would pass if the seam were simply gone."""
    tree = ast.parse(GATEWAY.read_text(encoding="utf-8"), filename=str(GATEWAY))
    assert _imported_roots(tree) & PROVIDER_PACKAGES


def test_the_gateway_imports_the_sdk_lazily() -> None:
    """An absent package or key must not break module import, so the import is not top-level.

    This is what lets the whole application -- and the whole test suite -- run with no
    credential and no SDK installed, which ADR-0002 requires of contributors and of CI.
    """
    tree = ast.parse(GATEWAY.read_text(encoding="utf-8"), filename=str(GATEWAY))
    top_level: set[str] = set()
    # Direct statements of the module body only -- ast.walk would descend into the very
    # function bodies the lazy import is supposed to be hiding in.
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top_level.add(node.module.split(".")[0])
    assert not (top_level & PROVIDER_PACKAGES), (
        "anthropic is imported at module level in gateway.py; it must be imported inside the "
        "live branch so that an absent package or key cannot break import."
    )


def test_no_application_module_imports_the_gateway_except_through_generate() -> None:
    """Services must not import ``app.ai``; ADR-0001 keeps that dependency edge acyclic.

    "To keep the edge acyclic, services must never import ``app.ai``; they receive Gateway
    results through the router layer." A service that imported the Gateway would also be a
    service that could pass its own unfiltered context, which is the property ADR-0001
    rejects a thin client wrapper for.
    """
    offenders: list[str] = []
    for path in _modules():
        if path.parts[-2] != "services":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                module = ",".join(alias.name for alias in node.names)
            if "app.ai" in module:
                offenders.append(f"{path.relative_to(APP_DIR)} imports {module}")
    assert not offenders, f"Services must not import app.ai (ADR-0001 consequences): {offenders}"


@pytest.mark.parametrize("package", sorted(PROVIDER_PACKAGES))
def test_the_gateway_module_imports_without_the_sdk_present(
    monkeypatch: pytest.MonkeyPatch, package: str
) -> None:
    """Importing the Gateway must not require the provider package to exist.

    ``sys.modules[package] = None`` is the documented way to make ``import package`` raise
    ``ImportError`` without uninstalling anything. The Gateway is then dropped from
    ``sys.modules`` and imported fresh, so this exercises a real import rather than
    returning the copy every other test already holds. ``monkeypatch`` restores both
    entries afterwards.
    """
    import importlib
    import sys

    monkeypatch.setitem(sys.modules, package, None)
    monkeypatch.delitem(sys.modules, "app.ai.gateway", raising=False)

    with pytest.raises(ImportError):
        importlib.import_module(package)

    module = importlib.import_module("app.ai.gateway")
    assert module.generate is not None
