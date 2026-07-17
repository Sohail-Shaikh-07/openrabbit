# OP-95 Optional Knowledge Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Define optional knowledge connector boundaries for MCP, web search, multi-repo context, Jira, and Linear without adding mandatory external services.

**Architecture:** Add a small dependency-free `knowledge` package that exposes typed connector request/item/protocol contracts and prompt-safe normalization. Keep all connector execution out of the review pipeline for this task; OP-95 establishes adapter interfaces and documentation only. Document how future connectors should enrich review context while preserving local-first defaults and fail-open behavior.

**Tech Stack:** Python 3.12, dataclasses, typing `Protocol`, enum, pytest, existing docs tests.

## Global Constraints

- Do not add hosted services, mandatory connector dependencies, telemetry, MCP runtime calls, web search calls, Jira calls, Linear calls, or multi-repo cloning.
- The default review, describe, ask, improve, index, memory, and eval flows must behave the same when no connector is configured.
- Connector snippets are untrusted context and must not be allowed to change output schemas, publish comments directly, or bypass diff grounding.
- Connector data must be sanitized before prompt use and must not include raw tokens, API keys, provider credentials, or unbounded text.
- Connector failures must be modeled as availability/health data for future orchestration, not as fatal review errors.
- Keep new interfaces additive and dependency-free.

---

## File Structure

- Create `src/knowledge/__init__.py`
  - Public exports for connector contracts.
- Create `src/knowledge/connectors.py`
  - Source kind enum, request/item dataclasses, connector protocol, sanitizer, and normalization helpers.
- Modify `pyproject.toml`
  - Include the new `knowledge` package and first-party import group.
- Create `tests/knowledge/test_connectors.py`
  - Contract, sanitizer, normalization, and protocol tests.
- Create `docs/knowledge-connectors.md`
  - Optional connector architecture, source-specific adapter boundaries, privacy rules, future config shape, and failure behavior.
- Modify `README.md`
  - Link the new connector design from the RAG/memory area and current capabilities table.
- Modify `tests/test_docs.py`
  - Assert README links to `docs/knowledge-connectors.md` and the guide documents required privacy/fail-open claims.

---

### Task 1: Dependency-Free Connector Contract

**Files:**
- Create: `src/knowledge/__init__.py`
- Create: `src/knowledge/connectors.py`
- Modify: `pyproject.toml`
- Create: `tests/knowledge/test_connectors.py`

**Interfaces:**
- Produces: `KnowledgeSourceKind`
- Produces: `KnowledgeConnectorRequest`
- Produces: `KnowledgeItem`
- Produces: `KnowledgeConnectorHealth`
- Produces: `KnowledgeConnector`
- Produces: `sanitize_knowledge_text(value: object, max_chars: int = 1200) -> str`
- Produces: `normalize_knowledge_items(items: Iterable[KnowledgeItem], *, max_items: int = 8, max_body_chars: int = 1200) -> list[KnowledgeItem]`

- [ ] **Step 1: Add failing tests**

Create `tests/knowledge/test_connectors.py`:

```python
from __future__ import annotations

from knowledge.connectors import (
    KnowledgeConnector,
    KnowledgeConnectorHealth,
    KnowledgeConnectorRequest,
    KnowledgeItem,
    KnowledgeSourceKind,
    normalize_knowledge_items,
    sanitize_knowledge_text,
)


class FakeConnector:
    name = "fake"
    source_kind = KnowledgeSourceKind.MCP

    def is_available(self) -> KnowledgeConnectorHealth:
        return KnowledgeConnectorHealth(name=self.name, source_kind=self.source_kind, available=True)

    def retrieve(self, request: KnowledgeConnectorRequest) -> list[KnowledgeItem]:
        return [
            KnowledgeItem(
                source_id="fake:item:1",
                source_kind=self.source_kind,
                title="Design note",
                body=f"Review {request.repo} PR {request.pr_number}",
            )
        ]


def test_fake_connector_satisfies_protocol() -> None:
    connector: KnowledgeConnector = FakeConnector()
    request = KnowledgeConnectorRequest(repo="owner/repo", pr_number=42)

    assert connector.is_available().available is True
    assert connector.retrieve(request)[0].body == "Review owner/repo PR 42"


def test_request_validates_pr_number_and_max_items() -> None:
    assert KnowledgeConnectorRequest(repo="owner/repo", pr_number=1, max_items=50).max_items == 50

    for kwargs in (
        {"repo": "", "pr_number": 1},
        {"repo": "owner/repo", "pr_number": 0},
        {"repo": "owner/repo", "pr_number": 1, "max_items": 0},
        {"repo": "owner/repo", "pr_number": 1, "max_items": 51},
    ):
        try:
            KnowledgeConnectorRequest(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {kwargs}")


def test_sanitize_knowledge_text_redacts_common_secrets_and_bounds_text() -> None:
    text = "token=super-secret-value and sk-abcdefghijklmnopqrstuvwxyz " + ("x" * 2000)

    sanitized = sanitize_knowledge_text(text, max_chars=80)

    assert "super-secret-value" not in sanitized
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in sanitized
    assert "token=[REDACTED]" in sanitized
    assert len(sanitized) <= 80
    assert sanitized.endswith("...")


def test_normalize_knowledge_items_filters_empty_and_bounds_items() -> None:
    items = [
        KnowledgeItem(
            source_id="one",
            source_kind=KnowledgeSourceKind.WEB_SEARCH,
            title="First",
            body="token=super-secret-value",
            score=0.2,
        ),
        KnowledgeItem(
            source_id="empty",
            source_kind=KnowledgeSourceKind.WEB_SEARCH,
            title="",
            body="",
        ),
        KnowledgeItem(
            source_id="two",
            source_kind=KnowledgeSourceKind.WEB_SEARCH,
            title="Second",
            body="Body",
            score=0.9,
        ),
    ]

    normalized = normalize_knowledge_items(items, max_items=1, max_body_chars=20)

    assert [item.source_id for item in normalized] == ["two"]
    assert "super-secret-value" not in normalized[0].body
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/knowledge/test_connectors.py -q --no-cov
```

Expected: import failure because `knowledge.connectors` does not exist.

- [ ] **Step 3: Implement connector contracts**

Create `src/knowledge/connectors.py`:

```python
"""Optional knowledge connector contracts for OpenRabbit."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

SECRET_REDACTION = "[REDACTED]"
MAX_KNOWLEDGE_TEXT_CHARS = 1200

_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b("
        r"github_pat_[A-Za-z0-9_]+|"
        r"ghp_[A-Za-z0-9_]+|"
        r"gho_[A-Za-z0-9_]+|"
        r"sk-[A-Za-z0-9_-]{20,}|"
        r"xox[baprs]-[A-Za-z0-9-]+"
        r")\b"
    ),
    re.compile(
        r"(?i)\b("
        r"(?:api[_-]?key|token|secret|password|authorization)"
        r"\s*[:=]\s*)"
        r"([^\s,;]{8,})"
    ),
)


class KnowledgeSourceKind(str, Enum):
    """Supported optional knowledge source categories."""

    MCP = "mcp"
    WEB_SEARCH = "web_search"
    MULTI_REPO = "multi_repo"
    ISSUE_TRACKER = "issue_tracker"
    DOCUMENT = "document"


@dataclass(frozen=True)
class KnowledgeConnectorRequest:
    """One bounded request for optional knowledge context."""

    repo: str
    pr_number: int
    head_sha: str = ""
    changed_paths: tuple[str, ...] = ()
    changed_symbols: tuple[str, ...] = ()
    query: str = ""
    max_items: int = 8
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.repo.strip():
            raise ValueError("repo is required")
        if self.pr_number <= 0:
            raise ValueError("pr_number must be positive")
        if self.max_items <= 0 or self.max_items > 50:
            raise ValueError("max_items must be between 1 and 50")


@dataclass(frozen=True)
class KnowledgeItem:
    """One sanitized knowledge item returned by a connector."""

    source_id: str
    source_kind: KnowledgeSourceKind
    title: str
    body: str
    url: str = ""
    repo: str = ""
    path: str = ""
    score: float | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeConnectorHealth:
    """Non-fatal availability state for one optional connector."""

    name: str
    source_kind: KnowledgeSourceKind
    available: bool
    reason: str = ""


@runtime_checkable
class KnowledgeConnector(Protocol):
    """Optional provider of extra review knowledge.

    Connectors must be read-only from OpenRabbit's perspective. They return
    untrusted context snippets and never publish comments or mutate pull requests.
    """

    name: str
    source_kind: KnowledgeSourceKind

    def is_available(self) -> KnowledgeConnectorHealth:
        """Return non-fatal connector health."""

    def retrieve(self, request: KnowledgeConnectorRequest) -> Sequence[KnowledgeItem]:
        """Return bounded knowledge items for one pull request."""


def sanitize_knowledge_text(value: object, max_chars: int = MAX_KNOWLEDGE_TEXT_CHARS) -> str:
    """Redact common secrets and bound text before prompt use."""
    if not isinstance(value, str) or max_chars <= 0:
        return ""
    body = value.strip()
    for pattern in _SECRET_PATTERNS:
        body = pattern.sub(_redact_secret_match, body)
    body = " ".join(body.split())
    if len(body) <= max_chars:
        return body
    return f"{body[: max_chars - 3].rstrip()}..."


def normalize_knowledge_items(
    items: Iterable[KnowledgeItem],
    *,
    max_items: int = 8,
    max_body_chars: int = MAX_KNOWLEDGE_TEXT_CHARS,
) -> list[KnowledgeItem]:
    """Return deterministic, prompt-safe connector items."""
    sanitized: list[KnowledgeItem] = []
    for item in items:
        title = sanitize_knowledge_text(item.title, max_chars=180)
        body = sanitize_knowledge_text(item.body, max_chars=max_body_chars)
        if not title and not body:
            continue
        sanitized.append(
            KnowledgeItem(
                source_id=item.source_id,
                source_kind=item.source_kind,
                title=title,
                body=body,
                url=sanitize_knowledge_text(item.url, max_chars=300),
                repo=sanitize_knowledge_text(item.repo, max_chars=120),
                path=sanitize_knowledge_text(item.path, max_chars=300),
                score=item.score,
                metadata={
                    sanitize_knowledge_text(key, max_chars=80): sanitize_knowledge_text(
                        value,
                        max_chars=300,
                    )
                    for key, value in item.metadata.items()
                },
            )
        )

    return sorted(
        sanitized,
        key=lambda item: (-(item.score or 0.0), item.source_kind.value, item.source_id),
    )[:max_items]


def _redact_secret_match(match: re.Match[str]) -> str:
    if match.lastindex and match.lastindex >= 2:
        return f"{match.group(1)}{SECRET_REDACTION}"
    return SECRET_REDACTION
```

- [ ] **Step 4: Export package API**

Create `src/knowledge/__init__.py`:

```python
"""Optional knowledge connector contracts."""

from knowledge.connectors import (
    KnowledgeConnector,
    KnowledgeConnectorHealth,
    KnowledgeConnectorRequest,
    KnowledgeItem,
    KnowledgeSourceKind,
    normalize_knowledge_items,
    sanitize_knowledge_text,
)

__all__ = [
    "KnowledgeConnector",
    "KnowledgeConnectorHealth",
    "KnowledgeConnectorRequest",
    "KnowledgeItem",
    "KnowledgeSourceKind",
    "normalize_knowledge_items",
    "sanitize_knowledge_text",
]
```

- [ ] **Step 5: Include package in Poetry and Ruff first-party list**

In `pyproject.toml`, add:

```toml
    { include = "knowledge", from = "src" },
```

and add `knowledge` to `known-first-party`.

- [ ] **Step 6: Run focused validation**

Run:

```powershell
python -m pytest tests/knowledge/test_connectors.py -q --no-cov
python -m ruff check src/knowledge tests/knowledge/test_connectors.py
python -m black --check src/knowledge tests/knowledge/test_connectors.py
python -m mypy
git diff --check
```

Expected: all pass.

- [ ] **Step 7: Commit Task 1**

Run:

```powershell
git add src/knowledge pyproject.toml tests/knowledge/test_connectors.py
git commit -m "feat(op-95): add optional knowledge connector contracts"
```

---

### Task 2: Connector Design Documentation

**Files:**
- Create: `docs/knowledge-connectors.md`
- Modify: `README.md`
- Modify: `tests/test_docs.py`

**Interfaces:**
- Consumes: connector names and contracts from Task 1.
- Produces: docs that future connector implementers can follow without adding mandatory services.

- [ ] **Step 1: Add failing docs tests**

Add to `tests/test_docs.py`:

```python
def test_readme_links_to_knowledge_connectors_guide() -> None:
    readme = (ROOT / "README.md").read_text(encoding="ascii")

    assert "[docs/knowledge-connectors.md](docs/knowledge-connectors.md)" in readme


def test_knowledge_connectors_guide_documents_contract() -> None:
    guide = (ROOT / "docs" / "knowledge-connectors.md").read_text(
        encoding="ascii"
    ).lower()

    for claim in (
        "mcp",
        "web search",
        "multi-repo",
        "jira",
        "linear",
        "optional",
        "local-first",
        "fail open",
        "read-only",
        "untrusted context",
        "no mandatory external services",
        "no raw tokens",
        "knowledgeconnector",
        "knowledgeconnectorrequest",
        "knowledgeitem",
        "health check",
    ):
        assert claim in guide
```

- [ ] **Step 2: Run docs tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_docs.py -q --no-cov
```

Expected: failure because the README link and guide do not exist yet.

- [ ] **Step 3: Create connector guide**

Create `docs/knowledge-connectors.md` with sections:

```markdown
# Optional Knowledge Connectors

OpenRabbit's default review loop remains local-first and service-free. Optional knowledge connectors are future adapters that can add context from MCP servers, web search, other repositories, Jira, Linear, or document systems after the user explicitly configures them.

## Contract

Connector implementations conform to `KnowledgeConnector` from `knowledge.connectors`.

- `KnowledgeConnectorRequest` describes the repository, PR number, head SHA, changed paths, changed symbols, query, and item limit.
- `KnowledgeItem` is one sanitized, bounded, read-only context snippet.
- `KnowledgeConnectorHealth` reports availability without mutating state.
- `normalize_knowledge_items` bounds text, redacts common tokens, and sorts snippets deterministically.

Connectors return untrusted context. They do not change the required model output schema, bypass changed-line grounding, or publish GitHub comments directly.

## Source Boundaries

### MCP

An MCP connector may query explicitly configured local or remote MCP servers for docs, tickets, or architecture notes. It must use user-approved server configuration and must fail open when a server is unavailable.

### Web Search

A web search connector may retrieve public documentation or advisories. It must be disabled by default, identify source URLs, and avoid sending private repository code as a search query unless the user explicitly opts in.

### Multi-Repo

A multi-repo connector may read sibling repositories configured by path or explicit repository handle. It must not auto-clone repositories or scan arbitrary directories. Returned snippets should identify the source repo and path.

### Jira And Linear

Issue tracker connectors may fetch linked work item title, state, labels, and a bounded body preview. They should not persist access tokens, and they should keep raw comments or attachments out of prompt context unless a future task adds explicit controls.

## Privacy And Failure Behavior

- No mandatory external services.
- No raw tokens, API keys, provider credentials, or unbounded text in connector output.
- Health checks are read-only.
- Connector failures produce warnings or unavailable health states and the review continues with diff, local memory, linked GitHub issues, and RAG context.
- Connector snippets are prompt guidance only and are always labeled by source.

## Future Configuration Shape

```yaml
knowledge:
  connectors:
    mcp:
      enabled: false
    web_search:
      enabled: false
    multi_repo:
      enabled: false
      repositories: []
    jira:
      enabled: false
      token_env: JIRA_API_TOKEN
    linear:
      enabled: false
      token_env: LINEAR_API_KEY
```

This shape is intentionally documented before runtime support. Enabling a connector in future versions must require installed optional dependencies and a passing health check.

## Prompt Flow

```text
PR diff
  -> local memory
  -> linked GitHub issues
  -> repository RAG
  -> optional knowledge connectors
  -> prompt context with labeled, bounded, untrusted snippets
```

## Acceptance Rules

A connector should not be added to runtime until it has:

- tests for health checks and fail-open behavior
- sanitization tests for prompt text
- docs for token environment variables and deletion/export behavior
- evaluation evidence that it improves review quality
- no mandatory dependency impact on default installs
```

- [ ] **Step 4: Link from README**

In the capability table, add `optional knowledge connector contracts` to the RAG row or a new `Knowledge connectors` row.

Near the memory/RAG docs paragraph, add:

```markdown
Optional MCP, web search, multi-repo, Jira, and Linear knowledge connector boundaries are documented in [docs/knowledge-connectors.md](docs/knowledge-connectors.md). These connectors are design-time extension points today; no external knowledge service is required for the default review loop.
```

- [ ] **Step 5: Run docs validation**

Run:

```powershell
python -m pytest tests/test_docs.py -q --no-cov
python -m ruff check tests/test_docs.py
python -m black --check tests/test_docs.py
git diff --check
```

Expected: all pass.

- [ ] **Step 6: Commit Task 2**

Run:

```powershell
git add docs/knowledge-connectors.md README.md tests/test_docs.py
git commit -m "docs(op-95): document optional knowledge connectors"
```

---

### Task 3: Final Verification And PR

**Files:**
- Modify: `.superpowers/sdd/progress.md`

**Interfaces:**
- Consumes: commits from Tasks 1 and 2.
- Produces: clean PR for issue #191.

- [ ] **Step 1: Run focused verification**

Run:

```powershell
python -m pytest tests/knowledge/test_connectors.py tests/test_docs.py -q --no-cov
python -m ruff check src/knowledge tests/knowledge/test_connectors.py tests/test_docs.py
python -m black --check src/knowledge tests/knowledge/test_connectors.py tests/test_docs.py
python -m mypy
git diff --check
```

Expected: all pass.

- [ ] **Step 2: Request final code review**

Create a review package from the branch base to HEAD and dispatch a reviewer. The reviewer must check:

- Connector contracts are dependency-free and additive.
- No runtime connector calls or mandatory services were added.
- Sanitization and item bounding are sufficient for prompt context.
- Docs clearly cover MCP, web search, multi-repo, Jira, Linear, privacy, and fail-open behavior.
- Package metadata includes the new package.

- [ ] **Step 3: Run full verification**

Run:

```powershell
python -m pytest
python -m ruff check $(git ls-files '*.py')
python -m black --check .
python -m mypy
python scripts/smoke_test.py
poetry build
```

Expected: all commands pass.

- [ ] **Step 4: Push and create PR**

Run:

```powershell
git push -u origin feature/op-95-knowledge-connector-design
gh pr create --title "[OP-95] Design optional knowledge connectors" --body "<project PR template body>"
```

PR body must include:

```text
Summary

Adds dependency-free optional knowledge connector contracts and documentation for MCP, web search, multi-repo, Jira, and Linear context sources.

What was fixed

* Added typed connector request, item, health, and protocol contracts.
* Added prompt-safe connector item sanitization and normalization helpers.
* Documented optional connector boundaries, privacy rules, fail-open behavior, and future config shape.
* Linked the connector design from README and covered it with docs tests.

Testing

* python -m pytest
* python -m ruff check <tracked Python files>
* python -m black --check .
* python -m mypy
* python scripts/smoke_test.py
* poetry build

Closes #191
```

- [ ] **Step 5: Finish the loop**

Wait for GitHub CI, merge only if green, update Notion OP-95 to Done, and sync local `main`.

