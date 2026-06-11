"""Tests for sage.backends.registry — backend registration, discovery, caching.

Covers findings [55]/[50]/[35] from the pycore audit cluster:
- register()/unregister() round-trip
- get_backend_class() success + KeyError on unknown name
- get_backend() caching then reset_backends() drop/close
- _discover_entry_points: explicit-wins-on-conflict and non-BaseBackend rejection
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sage_mcp.backends.base import BaseBackend
from sage_mcp.backends.registry import (
    available_backends,
    get_backend,
    get_backend_class,
    register,
    reset_backends,
    unregister,
)


class _FakeBackend(BaseBackend):
    """Minimal concrete BaseBackend for registry tests."""

    def get_collection(self, **kwargs):
        pass

    def close(self):
        pass


class _FakeBackend2(BaseBackend):
    def get_collection(self, **kwargs):
        pass

    def close(self):
        pass


def _isolate(func):
    """Decorator: run func with _fake_backend unregistered before+after."""
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        unregister("_fake_test_backend")
        unregister("_fake_test_backend2")
        try:
            return func(*args, **kwargs)
        finally:
            unregister("_fake_test_backend")
            unregister("_fake_test_backend2")

    return wrapper


@_isolate
def test_register_and_unregister_round_trip():
    register("_fake_test_backend", _FakeBackend)
    assert "_fake_test_backend" in available_backends()
    unregister("_fake_test_backend")
    assert "_fake_test_backend" not in available_backends()


@_isolate
def test_get_backend_class_success():
    register("_fake_test_backend", _FakeBackend)
    cls = get_backend_class("_fake_test_backend")
    assert cls is _FakeBackend


@_isolate
def test_get_backend_class_unknown_raises_key_error():
    with pytest.raises(KeyError, match="unknown backend"):
        get_backend_class("_no_such_backend_xyz")


@_isolate
def test_get_backend_caches_instance():
    register("_fake_test_backend", _FakeBackend)
    inst1 = get_backend("_fake_test_backend")
    inst2 = get_backend("_fake_test_backend")
    assert inst1 is inst2


@_isolate
def test_reset_backends_clears_instance_cache():
    register("_fake_test_backend", _FakeBackend)
    inst = get_backend("_fake_test_backend")
    assert inst is not None
    reset_backends()
    # After reset, a new instance is created on next get.
    inst2 = get_backend("_fake_test_backend")
    # Not the same object — cache was cleared.
    assert inst2 is not inst


@_isolate
def test_explicit_registration_wins_over_entry_point():
    """A name already in _explicit is skipped during entry-point discovery."""
    import sage_mcp.backends.registry as reg_mod

    # Pre-register so the name is in _explicit.
    register("_fake_test_backend", _FakeBackend)

    # Construct a fake entry point that would resolve to _FakeBackend2.
    fake_ep = MagicMock()
    fake_ep.name = "_fake_test_backend"
    fake_ep.load.return_value = _FakeBackend2

    with patch.object(reg_mod, "_discovered", False):
        with patch("importlib.metadata.entry_points") as mock_eps:
            eps_obj = MagicMock()
            eps_obj.select.return_value = [fake_ep]
            mock_eps.return_value = eps_obj
            # Force re-discovery.
            reg_mod._discovered = False
            reg_mod._discover_entry_points()

    # Explicit registration must still win — class stays _FakeBackend.
    assert get_backend_class("_fake_test_backend") is _FakeBackend
    # Entry point's load() must NOT have been called.
    fake_ep.load.assert_not_called()


@_isolate
def test_non_base_backend_entry_point_is_rejected():
    """An entry point resolving to a non-BaseBackend class is not registered."""
    import sage_mcp.backends.registry as reg_mod

    class _NotABackend:
        pass

    fake_ep = MagicMock()
    fake_ep.name = "_fake_test_backend2"
    fake_ep.load.return_value = _NotABackend

    with patch.object(reg_mod, "_discovered", False):
        with patch("importlib.metadata.entry_points") as mock_eps:
            eps_obj = MagicMock()
            eps_obj.select.return_value = [fake_ep]
            mock_eps.return_value = eps_obj
            reg_mod._discovered = False
            reg_mod._discover_entry_points()

    assert "_fake_test_backend2" not in available_backends()
