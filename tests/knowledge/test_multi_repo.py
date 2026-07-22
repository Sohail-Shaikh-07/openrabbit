from __future__ import annotations

from pathlib import Path

from configs.schema import KnowledgeConnectorsSettings
from knowledge.connectors import KnowledgeConnectorRequest
from knowledge.multi_repo import MultiRepoConnector


def _write(root: Path, relative: str, content: str) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _connector(
    body: dict[str, object],
    *,
    workspace_root: Path,
) -> MultiRepoConnector:
    settings = KnowledgeConnectorsSettings.model_validate(body)
    return MultiRepoConnector(settings.multi_repo, workspace_root=workspace_root)


def test_multi_repo_disabled_is_fail_open(tmp_path: Path) -> None:
    connector = _connector({"multi_repo": {"enabled": False}}, workspace_root=tmp_path)

    health = connector.is_available()
    items = connector.retrieve(KnowledgeConnectorRequest(repo="owner/repo", pr_number=1))

    assert health.available is False
    assert health.reason == "disabled"
    assert items == []


def test_multi_repo_requires_configured_repositories(tmp_path: Path) -> None:
    connector = _connector({"multi_repo": {"enabled": True}}, workspace_root=tmp_path)

    health = connector.is_available()

    assert health.available is False
    assert health.reason == "no repositories configured"


def test_multi_repo_reports_missing_local_paths_as_unavailable(tmp_path: Path) -> None:
    connector = _connector(
        {
            "multi_repo": {
                "enabled": True,
                "repositories": [{"name": "shared", "path": "missing-shared"}],
            }
        },
        workspace_root=tmp_path,
    )

    health = connector.is_available()
    items = connector.retrieve(KnowledgeConnectorRequest(repo="owner/repo", pr_number=1))

    assert health.available is False
    assert health.reason == "no configured repository paths are readable"
    assert items == []


def test_multi_repo_repo_handle_without_path_does_not_clone(tmp_path: Path) -> None:
    connector = _connector(
        {
            "multi_repo": {
                "enabled": True,
                "repositories": [{"name": "shared", "repo": "owner/shared"}],
            }
        },
        workspace_root=tmp_path,
    )

    health = connector.is_available()
    items = connector.retrieve(
        KnowledgeConnectorRequest(repo="owner/repo", pr_number=42, query="auth")
    )

    assert health.available is True
    assert health.reason == "configured"
    assert items == []
    assert not (tmp_path / "owner").exists()


def test_multi_repo_retrieves_source_labeled_local_context(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    _write(
        shared,
        "src/auth.py",
        "def require_admin(user):\n    return user.is_admin\n",
    )
    _write(
        shared,
        "docs/export.md",
        "# Export Auth\nRequire admin auth for export endpoints. token: secret-token\n",
    )
    connector = _connector(
        {
            "multi_repo": {
                "enabled": True,
                "repositories": [{"name": "shared-core", "path": "shared", "repo": "owner/shared"}],
            }
        },
        workspace_root=tmp_path,
    )

    items = connector.retrieve(
        KnowledgeConnectorRequest(
            repo="owner/repo",
            pr_number=42,
            changed_paths=("src/auth.py",),
            query="export admin auth",
            max_items=5,
        )
    )

    assert items
    assert items[0].source_kind.value == "multi_repo"
    assert items[0].source_id.startswith("multi_repo:")
    assert items[0].repo == "owner/shared"
    assert items[0].metadata["repo"] == "shared-core"
    assert items[0].metadata["repo_handle"] == "owner/shared"
    assert items[0].metadata["source"] == "local_path"
    assert items[0].metadata["trust"] == "untrusted"
    bodies = "\n".join(item.body for item in items)
    assert "secret-token" not in bodies
    assert "[REDACTED]" in bodies
    assert any(item.path in {"src/auth.py", "docs/export.md"} for item in items)


def test_multi_repo_respects_request_and_configured_limits(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    _write(shared, "docs/one.md", "# One\nauth one\n")
    _write(shared, "docs/two.md", "# Two\nauth two\n")
    _write(shared, "docs/three.md", "# Three\nauth three\n")
    connector = _connector(
        {
            "multi_repo": {
                "enabled": True,
                "max_items": 2,
                "repositories": [{"name": "shared", "path": "shared"}],
            }
        },
        workspace_root=tmp_path,
    )

    items = connector.retrieve(
        KnowledgeConnectorRequest(repo="owner/repo", pr_number=42, query="auth", max_items=3)
    )

    assert len(items) == 2


def test_multi_repo_skips_hidden_generated_and_oversized_files(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    _write(shared, ".github/copilot-instructions.md", "auth hidden")
    _write(shared, "dist/generated.py", "def auth(): pass")
    _write(shared, "src/large.py", "auth = True\n" * 9000)
    _write(shared, "src/service.py", "def auth_service():\n    return True\n")
    connector = _connector(
        {
            "multi_repo": {
                "enabled": True,
                "repositories": [{"name": "shared", "path": "shared"}],
            }
        },
        workspace_root=tmp_path,
    )

    items = connector.retrieve(
        KnowledgeConnectorRequest(repo="owner/repo", pr_number=42, query="auth", max_items=10)
    )

    paths = {item.path for item in items}
    assert paths == {"src/service.py"}


def test_multi_repo_fails_open_when_scanner_cannot_read_repo(tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("x", encoding="utf-8")
    connector = _connector(
        {
            "multi_repo": {
                "enabled": True,
                "repositories": [{"name": "bad", "path": "not-a-dir"}],
            }
        },
        workspace_root=tmp_path,
    )

    items = connector.retrieve(
        KnowledgeConnectorRequest(repo="owner/repo", pr_number=42, query="auth")
    )

    assert items == []
