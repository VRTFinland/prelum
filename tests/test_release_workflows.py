from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
DOCS_WORKFLOW = ROOT / ".github" / "workflows" / "docs.yml"


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
