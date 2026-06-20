"""Template content written by ``openrabbit init``.

Each constant is the *initial* content for the matching file under
``.codereviewer/`` in a target repository. Templates are intentionally short
prose plus headings so a maintainer can edit them right away. Their actual
semantics (which agent reads what) are nailed down in Phases 3 and 4.
"""

from __future__ import annotations

from typing import Final

CONFIG_YML: Final[str] = """\
# OpenRabbit repository configuration.
#
# Edit this file to control which reviews run, which model is used,
# and how often the polling service checks GitHub.

review:
  security: true
  performance: true
  architecture: true
  bug: true
  test_coverage: true
  style: false

model:
  provider: ollama
  model_name: openrabbit-reviewer-v1
  base_model: qwen2.5-coder:7b-instruct

polling:
  interval_seconds: 60

github:
  # GITHUB_TOKEN can also come from the environment. The token never leaves
  # this machine.
  token_env: GITHUB_TOKEN

repository:
  # Default repository OpenRabbit watches when --repo is not passed.
  # target: owner/repo
"""

ARCHITECTURE_MD: Final[str] = """\
# Architecture

Describe the high-level shape of this repository.

## Services / modules

- ...

## Layering rules

- ...

## Non-negotiables

- ...
"""

CODING_RULES_MD: Final[str] = """\
# Coding rules

Project-wide conventions OpenRabbit will use when reviewing pull requests.

## Naming

- ...

## Error handling

- ...

## Logging

- ...
"""

SECURITY_RULES_MD: Final[str] = """\
# Security rules

Patterns OpenRabbit should always flag, and patterns it should never flag.

## Always flag

- Secrets committed to the repo
- Unparameterized SQL
- Unsanitized user input reaching the shell
- Disabled TLS verification

## Acceptable patterns

- ...
"""

REVIEW_EXAMPLES_MD: Final[str] = """\
# Review examples

Worked examples of good and bad review comments for this repository.
OpenRabbit retrieves these examples during a review to match team style.

## Good

> Potential null dereference: `user` may be `None` when this branch runs.
> Add a guard clause or change the caller contract.

## Bad

> LGTM
"""

IGNORE_TXT: Final[str] = """\
# Files and globs OpenRabbit should not index or review.
# One pattern per line. Lines starting with # are comments.

# Vendored / generated
**/node_modules/**
**/dist/**
**/build/**
**/.venv/**
**/__pycache__/**

# Lockfiles
poetry.lock
package-lock.json
yarn.lock
"""

TEMPLATES: Final[dict[str, str]] = {
    "config.yml": CONFIG_YML,
    "architecture.md": ARCHITECTURE_MD,
    "coding_rules.md": CODING_RULES_MD,
    "security_rules.md": SECURITY_RULES_MD,
    "review_examples.md": REVIEW_EXAMPLES_MD,
    "ignore.txt": IGNORE_TXT,
}
