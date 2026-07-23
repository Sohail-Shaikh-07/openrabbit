"""Documentation contract tests."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_links_to_ast_review_controls_guide() -> None:
    readme = (ROOT / "README.md").read_text(encoding="ascii")

    assert "[AST review controls](docs/ast-review-controls.md)" in readme
    assert "ast_instructions:" in readme


def test_readme_links_to_eval_reporting_guide() -> None:
    readme = (ROOT / "README.md").read_text(encoding="ascii")

    assert "[docs/eval-reporting.md](docs/eval-reporting.md)" in readme


def test_readme_links_to_knowledge_connectors_guide() -> None:
    readme = (ROOT / "README.md").read_text(encoding="ascii")

    assert "[docs/knowledge-connectors.md](docs/knowledge-connectors.md)" in readme


def test_readme_links_to_context_precision_guide() -> None:
    readme = (ROOT / "README.md").read_text(encoding="ascii")

    assert "[docs/context-precision.md](docs/context-precision.md)" in readme


def test_ast_review_controls_guide_documents_contract() -> None:
    guide = (ROOT / "docs" / "ast-review-controls.md").read_text(encoding="ascii").lower()

    def assert_documents(*claims: str) -> None:
        for claim in claims:
            assert claim in guide, f"Missing documentation claim: {claim}"

    assert_documents(
        "ast_instructions",
        "shell-style glob matching",
        "*` matches any sequence",
        "?` matches one character",
        "bracket expressions",
        "overlapping rules are additive",
        "addition-only",
        "symbol overlap",
        "deleted-only declarations",
        "context-only symbols",
    )

    for extension, language in (
        (".py", "python"),
        (".js", "javascript"),
        (".jsx", "javascript"),
        (".ts", "typescript"),
        (".tsx", "typescript"),
    ):
        assert extension in guide
        assert language in guide

    for symbol in ("function", "method", "class"):
        assert symbol in guide

    assert_documents(
        "`524288` decoded source bytes",
        "at most four source requests run concurrently",
        "when `ast_instructions` is empty",
        "no ast source requests",
    )

    assert_documents(
        "provenance",
        "file path",
        "line span",
        "symbol kind",
        "symbol name",
        "unavailable",
        "oversized",
        "unparsable",
        "sanitized warning",
        "falls back",
        "path",
        "diff",
        "context",
    )

    assert_documents(
        "untrusted repository text",
        "prompt guidance only",
        "source code is never imported or executed",
        "code",
        "configuration",
        "rules",
        "never executed",
        "shell",
        "commands",
        "plugins",
    )

    for example in (
        "security guidance for changed authorization methods",
        'path: "src/auth/**"',
        "test guidance for changed test functions",
        'path: "tests/**"',
        "architecture guidance for changed service classes",
        'path: "src/services/**"',
    ):
        assert example in guide


def test_knowledge_connectors_guide_documents_contract() -> None:
    guide = (ROOT / "docs" / "knowledge-connectors.md").read_text(encoding="ascii").lower()

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
        "setup checklist",
        "environment variables and permissions",
        "connector-health troubleshooting",
        "review-time troubleshooting",
        "jira_api_token",
        "linear_api_key",
        "poetry install --with connectors",
        "allow_private_code_queries",
        "one openrabbit summary comment",
    ):
        assert claim in guide


def test_context_precision_guide_documents_troubleshooting_contract() -> None:
    guide = (ROOT / "docs" / "context-precision.md").read_text(encoding="ascii").lower()

    for claim in (
        "changed-line evidence",
        "compressed diff evidence",
        "source budgets",
        "retrieval reasons",
        "changed_file",
        "changed_symbol",
        "related_test",
        "nearby_path",
        "scoped_guideline",
        "architecture_doc",
        "connector relevance",
        "weak_connector_relevance",
        "connector_item_limit",
        "large_low_risk_files",
        "context_diagnostics",
        "source_packing",
        "prompt_packing",
        "troubleshooting missing context",
        "troubleshooting noisy context",
        "troubleshooting budget pressure",
        "eval interpretation",
        "default_v1_7_context_precision_corpus",
        "privacy boundaries",
        "fail open",
    ):
        assert claim in guide

    for budget in ("3000", "6000", "4500", "1200", "1600", "2000"):
        assert budget in guide


def test_readme_documents_connector_setup_quickstart() -> None:
    readme = (ROOT / "README.md").read_text(encoding="ascii").lower()

    for claim in (
        "connector setup is intentionally explicit",
        "jira_api_token",
        "linear_api_key",
        "openrabbit connector-health --workspace .",
        "setup templates",
        "required permissions",
        "write-mode boundaries",
        "health-check troubleshooting",
    ):
        assert claim in readme


def test_v1_7_context_precision_plan_documents_release_scope() -> None:
    plan = (ROOT / "docs" / "release-v1.7-plan.md").read_text(encoding="ascii").lower()
    gap = (ROOT / "docs" / "pr-agent-gap-analysis.md").read_text(encoding="ascii")

    for claim in (
        "context precision",
        "changed-line evidence first",
        "source budgets",
        "connector relevance scoring",
        "context precision eval",
        "security and privacy regressions",
        "op-113",
        "op-121",
    ):
        assert claim in plan
    assert "release-v1.7-plan.md" in gap
