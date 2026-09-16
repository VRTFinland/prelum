"""The service refuses to start rather than answering every request with a misleading 422.

Every way the prlimit wrapper can fail — missing, denied by seccomp, handed an argument getopt will
not attach — ends in a positive exit status, and the renderer reads a positive status as the
caller's template failing to compile. A misconfigured memory limit would therefore produce a stream
of 422s blaming callers, with nothing in the logs naming the deployment as the cause. The only
place that can be caught is before the first request.
"""

import subprocess
from pathlib import Path

import pytest

from app.core import constants
from app.main import create_app

# These tests construct several different configurations, and the dependencies are @cache'd.
pytestmark = pytest.mark.usefixtures("reset_dependency_caches")


def test_an_unconfigured_limit_probes_nothing(monkeypatch: pytest.MonkeyPatch):
    """The default is off, so the unit suite must not fork a probe on every app construction."""

    def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("no probe should run when no limit is configured")

    monkeypatch.setattr(subprocess, "run", fail_if_called)

    _ = create_app()


def test_a_configured_limit_that_the_wrapper_rejects_stops_the_service(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(constants, "PRLIMIT_PATH", Path("/bin/sh"))
    monkeypatch.setenv("PRELUM_MAX_RENDER_MEMORY_BYTES", str(1024 * 1024 * 1024))
    monkeypatch.setenv("PRELUM_API_TOKEN", "startup-check-token")

    def failing_probe(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args=[], returncode=127, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", failing_probe)

    with pytest.raises(RuntimeError, match="render memory limit"):
        _ = create_app()


def test_a_configured_limit_the_wrapper_accepts_starts_normally(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(constants, "PRLIMIT_PATH", Path("/bin/sh"))
    monkeypatch.setenv("PRELUM_MAX_RENDER_MEMORY_BYTES", str(1024 * 1024 * 1024))
    monkeypatch.setenv("PRELUM_API_TOKEN", "startup-check-token")
    probes: list[object] = []

    def passing_probe(args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        probes.append(args)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", passing_probe)

    _ = create_app()

    # The probe must exercise the argv the renderer will actually build, or it proves nothing about
    # it: the attached --data form is the part most likely to be broken by a well-meaning refactor.
    (probe_args,) = probes
    assert isinstance(probe_args, list)
    assert probe_args[:3] == ["/bin/sh", "--data=1073741824", "--"]


def test_a_probe_that_hangs_is_reported_as_a_startup_failure(monkeypatch: pytest.MonkeyPatch):
    """
    TimeoutExpired is not an OSError, so it escaped the handler that promises a RuntimeError.

    The operations guide tells operators to look for the probe's message in the log; a raw
    traceback from a hung wrapper would not carry it.
    """
    monkeypatch.setattr(constants, "PRLIMIT_PATH", Path("/bin/sh"))
    monkeypatch.setenv("PRELUM_MAX_RENDER_MEMORY_BYTES", str(1024 * 1024 * 1024))
    monkeypatch.setenv("PRELUM_API_TOKEN", "startup-check-token")

    def hanging_probe(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="prlimit", timeout=30)

    monkeypatch.setattr(subprocess, "run", hanging_probe)

    with pytest.raises(RuntimeError, match="render memory limit probe"):
        _ = create_app()


def test_an_explicitly_emptied_memory_limit_says_so_at_startup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """
    Emptying the variable is the documented way to disable the bound, and it must not be quiet.

    Before an empty value was accepted, a chart that rendered the variable from an undefined value
    crash-looped on an integer parse error and the operator fixed it. Now the pod boots healthy with
    no per-render bound, and the omission would otherwise surface much later as the very thing the
    setting prevents — with the container-level kill that follows reported as the caller's fault,
    because the classification keys off the same attribute.
    """
    monkeypatch.setenv("PRELUM_MAX_RENDER_MEMORY_BYTES", "")
    monkeypatch.setenv("PRELUM_API_TOKEN", "startup-check-token")

    _ = create_app()

    assert "startup.render_memory_unbounded" in capsys.readouterr().out


def test_an_absent_memory_limit_is_not_announced(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    """Unset is the default on every development machine; only a deliberate opt-out is notable."""
    monkeypatch.delenv("PRELUM_MAX_RENDER_MEMORY_BYTES", raising=False)
    monkeypatch.setenv("PRELUM_API_TOKEN", "startup-check-token")

    _ = create_app()

    assert "startup.render_memory_unbounded" not in capsys.readouterr().out
