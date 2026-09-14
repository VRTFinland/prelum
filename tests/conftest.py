"""Shared fixtures for the prelum test suite."""

import os

# Settings is fail-closed: without an explicit dev environment it refuses to construct. Declaring
# it here rather than per-test keeps the production rule intact for the tests that assert on it,
# which clear the variable through monkeypatch.
os.environ.setdefault("PRELUM_ENVIRONMENT", "test")

from collections.abc import Callable

import pytest

from app.core.config import Settings
from app.render.renderer import TypstRenderer


@pytest.fixture
def make_renderer() -> Callable[..., TypstRenderer]:
    """
    Build a renderer with per-test settings overrides.

    Keeps the collaborator wiring in one place: every test that needs a renderer otherwise repeats
    the same Settings/TypstRenderer pair.
    """

    def factory(**settings_overrides: object) -> TypstRenderer:
        settings = Settings(**settings_overrides)
        return TypstRenderer(settings)

    return factory
