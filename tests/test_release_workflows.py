import tomllib
from pathlib import Path

import pytest

from app.core import constants

ROOT = Path(__file__).resolve().parent.parent
BUILD_WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
DOCS_WORKFLOW = ROOT / ".github" / "workflows" / "docs.yml"
DOCKERFILE = ROOT / "Dockerfile"
PYPROJECT = ROOT / "pyproject.toml"


def test_build_workflow_does_not_publish_direct_tag_pushes():
    workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")

    assert 'tags: ["v*"]' not in workflow


def test_completed_release_retry_skips_image_publication():
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "already_released: ${{ steps.release.outputs.already_released }}" in workflow
    assert "if: needs.prepare.outputs.already_released != 'true'" in workflow


def test_docs_workflow_builds_through_the_shared_make_target():
    workflow = DOCS_WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "make docs-build" in workflow
    assert "uv sync --locked --group docs" in workflow


def test_docs_workflow_limits_publication_and_write_permissions():
    workflow = DOCS_WORKFLOW.read_text(encoding="utf-8")

    assert "github.event.repository.visibility == 'public'" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "github.event_name != 'pull_request'" in workflow
    assert workflow.count("pages: write") == 1
    assert workflow.count("id-token: write") == 1


def test_the_release_job_can_resolve_the_repository_without_a_checkout():
    """
    The job that creates the GitHub release never checks out the repository.

    Without GH_REPO, gh falls back to inferring the repository from a git remote and fails with
    "not a git repository" — which is how the 1.0.1 release published its tag and image and then
    stopped short of the release itself. It also silently defeats the existence check that makes a
    retry a no-op, because that check discards its own error output.
    """
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "GH_REPO: ${{ github.repository }}" in workflow


def test_the_image_carries_the_file_the_version_is_read_from():
    """
    The final stage copies app/ and the virtualenv, and nothing else, by design.

    app/core/constants.py reads the version from pyproject.toml at import, so dropping this one
    COPY would take the service down on startup rather than degrade quietly — but it would take it
    down in production, having passed every test. Pin the copy here instead.
    """
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "COPY --from=builder /prelum/pyproject.toml /prelum/pyproject.toml" in dockerfile


def test_the_reported_version_is_the_one_pyproject_spells():
    """One file spells the version; `uv version X.Y.Z` is the only thing that needs to change it."""
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    assert pyproject["project"]["version"] == constants.VERSION


def test_a_version_that_cannot_be_read_fails_with_the_path_that_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A traceback naming the missing file beats a service reporting a plausible wrong version."""
    missing = tmp_path / "pyproject.toml"
    monkeypatch.setattr(constants, "PYPROJECT", missing)

    with pytest.raises(RuntimeError, match=str(missing)):
        _ = constants._project_version()


def test_releases_are_cut_from_a_release_branch():
    """
    Releases branch off dev, are tagged there, and reach main by merge afterwards.

    The guard is what stops a release from being dispatched against dev's moving head, where the
    commit that gets tagged is whatever landed last rather than the one prepared for release.
    """
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert '"$GITHUB_REF" != refs/heads/release/*' in workflow
    assert '"refs/heads/main"' not in workflow


def test_dev_publishes_its_own_tag_without_taking_over_latest():
    """
    `latest` follows releases, not dev.

    docker/metadata-action derives `latest` from the semver rules alone, so the branch rule gives
    the dev head the `dev` tag and a bare `docker pull` keeps resolving to the newest release.
    """
    workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")

    assert 'branches: ["main", "dev"]' in workflow
    assert "type=ref,event=branch" in workflow
    assert "type=raw,value=latest" not in workflow


def test_documentation_is_built_on_dev_but_published_only_from_main():
    workflow = DOCS_WORKFLOW.read_text(encoding="utf-8")

    assert 'branches: ["main", "dev"]' in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
