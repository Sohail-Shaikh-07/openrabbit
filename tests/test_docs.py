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
