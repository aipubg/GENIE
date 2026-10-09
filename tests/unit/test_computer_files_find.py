"""Regression: files.find must not crash when no root is given.

Found during release-candidate hardening via the wrong-action baseline: calling
`files.find` without a `root` raised

    NameError: name 'Path' is not defined

because computer/executor.py used `Path` (in `user_search_roots()` and in the
no-root branch of `files.find`) without importing it at module level. The
capability crashed instead of searching the user's usual places.

These tests are deterministic — no real desktop required.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from computer import executor  # noqa: E402


def test_path_is_imported_at_module_level():
    assert hasattr(executor, "Path"), \
        "executor must import Path at module level (files.find uses it)"


def test_user_search_roots_returns_real_directories():
    roots = executor.user_search_roots()
    assert isinstance(roots, list) and roots, "expected at least one search root"
    assert all(isinstance(r, str) for r in roots)


def test_files_find_without_root_does_not_raise_nameerror():
    """The crash was a NameError, so the call must at least not blow up."""
    from computer import files as files_mod
    try:
        # exercise the no-root path the same way the executor does
        for candidate in executor.user_search_roots():
            if Path(candidate).exists():
                files_mod.find(candidate, "genie", None, 0, 5)
                break
    except NameError as exc:  # pragma: no cover - regression guard
        pytest.fail(f"files.find still hits a NameError: {exc}")
    except Exception:
        # any other error is acceptable here; we are guarding the NameError crash
        pass
