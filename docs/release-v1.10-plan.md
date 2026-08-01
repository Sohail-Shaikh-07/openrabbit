# OpenRabbit v1.10 Webhook And Server Mode Plan

OpenRabbit v1.10 focuses on optional self-hosted GitHub webhook and server mode after the v1.9 repository maintenance automation release. The release should add push-based review automation for teams that do not want a long-running polling loop, while preserving the current local-first CLI, GitHub Action, and daemon workflows.

## Goals

- Add disabled-by-default GitHub webhook configuration with explicit secrets and event allowlists.
- Verify GitHub webhook signatures before any event is parsed or dispatched.
- Add an optional FastAPI server entrypoint for local or self-hosted webhook delivery.
- Reuse the existing review, interactive PR command, and maintenance command logic through shared command dispatch.
- Track webhook delivery IDs and processing state so retries are idempotent and replayable.
- Preserve local-first privacy boundaries, explicit permissions, bounded payload handling, and polling compatibility.

## Planned Work

| Task | Focus | Outcome |
| --- | --- | --- |
| OP-141 | v1.10 planning | Release plan, task sequence, and repository roadmap links |
| OP-142 | Webhook configuration and signatures | Secret configuration, GitHub signature verification, event allowlists, and tests |
| OP-143 | FastAPI server entrypoint | Optional `openrabbit server` command with health and GitHub webhook routes |
| OP-144 | Shared webhook dispatch | Pull request and issue comment webhook events routed into existing review and PR command flows |
| OP-145 | Delivery state and idempotency | Safe retry handling with delivery IDs, status tracking, and replay metadata |
| OP-146 | Security and regression tests | Signature failures, unsupported events, fork/privacy guards, replay behavior, and polling compatibility |
| OP-147 | Docs and deployment guide | GitHub webhook setup, self-hosted deployment, troubleshooting, and polling fallback docs |
| OP-148 | v1.10.0 release | Version bump, changelog, release notes, CI, tag, and release artifacts |

## Progress Notes

- OP-141 creates the v1.10 planning track, seeds the task sequence, and links the repository roadmap to this plan.
- OP-142 adds disabled-by-default webhook configuration, environment-only secret resolution, validated GitHub event allowlists, bounded payload settings, and deterministic fail-closed HMAC-SHA256 signature verification helpers.
- OP-143 adds explicit FastAPI and Uvicorn runtime dependencies, a localhost-default `openrabbit server` entrypoint, a public health route, and a fail-closed GitHub webhook intake route. Accepted deliveries remain undispatched until OP-144, and existing `openrabbit start` polling behavior remains unchanged.
- OP-144 should translate supported GitHub webhook events into existing review and PR command requests instead of duplicating review, ask, improve, learn, summary, pause, resume, or ignore logic.
- OP-145 should persist delivery state under the workspace so repeated GitHub deliveries can be skipped, retried, or inspected without duplicate publishing.
- OP-146 should add focused security and regression coverage for invalid signatures, missing secrets, unsupported events, payload bounds, fork/privacy cases, replay handling, and compatibility with daemon polling.
- OP-147 should document local and self-hosted webhook setup, GitHub secret configuration, expected permissions, operational troubleshooting, and when polling remains the better fit.
- OP-148 should bump the package to `1.10.0` and add release notes, changelog archive, release artifact checks, and release-readiness validation.

## Scope Notes

Webhook mode should be an additional operating mode, not a replacement for local CLI, GitHub Actions, or polling. Users should be able to keep running OpenRabbit exactly as they do today unless they explicitly configure and start the server.

The webhook receiver should validate the event before dispatching work. Signature checks, event allowlists, payload size limits, repository matching, and command parsing should happen before any model call or GitHub mutation. Dispatch should reuse the existing command paths so `/openrabbit review`, `/openrabbit summary`, `/openrabbit ask`, `/openrabbit improve`, `/openrabbit learn`, `/openrabbit pause`, `/openrabbit resume`, and `/openrabbit ignore` keep one source of behavior.

Delivery state should stay local to the workspace. It should store enough metadata to avoid duplicate reviews and diagnose retries, but it should not persist raw secrets, raw model prompts, or unbounded webhook bodies.

## Non Goals

- No hosted OpenRabbit service requirement.
- No GitHub App marketplace flow in this release.
- No replacement of polling or GitHub Actions workflows.
- No broad provider, model, RAG, or connector rewrite.
- No automatic mutation without the same explicit command permissions already required by CLI and automation workflows.
- No dashboard or HTML reporting requirement in this release.

## Validation Plan

- Keep full local gates: `python -m ruff check src tests`, `python -m black --check src tests`, `python -m mypy src`, `python -m pytest`, `python -m build`, and CLI smoke checks.
- Add focused unit tests for signature verification, config loading, event allowlists, payload bounds, delivery idempotency, and dispatch mapping.
- Add FastAPI route tests for health checks, accepted GitHub events, rejected signatures, unsupported event names, and malformed payloads.
- Use `testing-openrabbit` for external smoke checks of the installed package, including version checks and a dry-run command that confirms existing CLI flows still work.
- Keep release readiness tied to green CI, local main sync, local reinstall, and a successful v1.10.0 release workflow.
