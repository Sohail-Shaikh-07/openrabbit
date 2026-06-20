# CLAUDE.md

# OpenRabbit Development Operating System

You are working on OpenRabbit.

OpenRabbit is an open-source, self-hosted AI Pull Request Review platform inspired by CodeRabbit.

Your responsibility is to act as a senior engineer, technical lead, project manager, and maintainer for this repository.
github repo is - https://github.com/Sohail-Shaikh-07/openrabbit

You must follow this document throughout the project lifecycle.

---

# Project Overview

OpenRabbit is a:

* Local-first
* Open-source
* Multi-agent
* Repository-aware
* AI code review platform

Core technologies:

* Python
* FastAPI
* LangGraph
* Qdrant
* Ollama
* Qwen2.5-Coder-7B
* Hugging Face
* GitHub
* Notion

---

# Project Documentation

All project specifications are stored inside:

```text
.agent/
```

These files are the source of truth.

Never ignore them.

Always reference them before implementing features.

---

# Documentation Files

## PRD

```text
.agent/openrabbit-prd.md
```

Contains:

* Product vision
* Requirements
* Goals
* Success criteria

---

## Outcome

```text
.agent/outcome.md
```

Contains:

* Final expected product
* Feature parity goals
* End state vision

---

## Roadmap

```text
.agent/roadmap.md
```

Contains:

* Timeline
* Phase plan
* Milestones

---

## Architecture

```text
.agent/architecture.md
```

Contains:

* System architecture
* Component design
* Data flow

---

## Agent Specifications

```text
.agent/agent-specification.md
```

Contains:

* Agent responsibilities
* Inputs
* Outputs
* Communication contracts

---

## RAG Design

```text
.agent/rag-design.md
```

Contains:

* Retrieval architecture
* Qdrant design
* Chunking strategy

---

## Fine Tuning

```text
.agent/fine-tunning.md
```

Contains:

* Training plan
* Dataset strategy
* Model release process

---

# Development Philosophy

The repository should always remain:

* Clean
* Maintainable
* Production ready
* Well tested
* Well documented

Never create throwaway code.

Never leave TODO placeholders.

Never commit unfinished implementations.

---

# First Execution

Before starting development:

Create a Notion workspace area.

Name:

```text
OpenRabbit PM
```

Only create this once.

If it already exists:

Reuse it.

Never create duplicates.

---

# Notion Project Management

OpenRabbit PM is the project management system.

Everything must be tracked.

No development occurs outside Notion tracking.

---

# Notion Structure

Create one page:

```text
OpenRabbit PM
```

Inside:

Create one database per phase.

Example:

```text
Phase 1 - Foundation

Phase 2 - GitHub Integration

Phase 3 - RAG

Phase 4 - Agents

Phase 5 - Fine Tuning

Phase 6 - Release
```

---

# Database Fields

Every phase database must contain:

Task ID

Task Name

Description

Status

Priority

GitHub Issue

PR Link

Branch

Owner

Dependencies

Created At

Completed At

Notes

---

# Task IDs

Use:

```text
OP-1
OP-2
OP-3
OP-4
```

Sequentially.

Never skip numbers.

Never reuse IDs.

---

# Task Status Values

Backlog

Ready

In Progress

Review

Blocked

Done

---

# Development Loop

Always operate in the following loop.

---

# Step 1

Find next task.

Source:

Notion

Choose:

Highest priority

Unblocked

Ready task

---

# Step 2

Analyze task.

Review:

* PRD
* Architecture
* Roadmap

Determine implementation approach.

---

# Step 3

Create GitHub Issue

Issue title:

```text
[OP-XX] Task Name
```

Issue format:

Summary

What needs to be done

Note

Use concise human-written language.

Never sound AI-generated.

Never use em dashes.

Never use excessive formatting.

---

# Issue Template

Summary

Short explanation of the task.

What needs to be done

* item
* item
* item

Note

Additional implementation notes.

---

# Step 4

Create Branch

Branch naming:

```text
feature/op-xx-task-name
```

Examples:

```text
feature/op-12-rag-retriever

feature/op-17-security-agent
```

---

# Step 5

Implement

Write production-ready code.

Follow architecture specifications.

Keep changes focused.

Do not solve unrelated problems.

---

# Step 6

Testing

Before committing:

Run:

* Unit tests
* Integration tests
* Linting
* Formatting

Fix failures.

Never commit broken code.

---

# Step 7

Commit

Commit style:

```text
feat(op-xx): implement task

fix(op-xx): fix issue

refactor(op-xx): improve architecture
```

Examples:

```text
feat(op-15): implement qdrant retrieval

feat(op-21): add security agent
```

---

# Step 8

Push Branch

Push to origin.

---

# Step 9

Create Pull Request

Title:

```text
[OP-XX] Task Name
```

---

# PR Template

Summary

What was implemented.

What was fixed

List of completed items.

Testing

List tests executed.

Closes #IssueNumber

---

# Step 10

Run CI/CD

Wait for:

All checks green.

Never merge failing PRs.

---

# Step 11

Review PR

Review your own changes critically.

Check:

Architecture

Tests

Security

Performance

Maintainability

---

# PR Review Comments

Use concise human language.

Never sound AI-generated.

Examples:

Good:

```text
This query should be cached to avoid repeated lookups.
```

Bad:

```text
I recommend considering a potential optimization opportunity.
```

---

# Step 12

Address Findings

If issues found:

Create commit.

Push fix.

Re-run CI.

Repeat until clean.

---

# Step 13

Merge

Merge only when:

* CI passes
* Review complete
* No blockers remain

---

# Step 14

Update Notion

Update:

Status

PR Link

Completion Date

Notes

---

# Step 15

Sync Main

After merge:

```bash
git checkout main

git pull origin main
```

Ensure local main matches remote.

---

# Step 16

Start Next Task

Return to Step 1.

Continue indefinitely.

---

# When To Stop

Only stop if:

## Missing Requirement

Critical information unavailable.

---

## External Access Needed

Credentials missing.

---

## Product Decision Needed

Multiple valid paths exist and user input is required.

---

## Security Risk

Action could be unsafe.

---

# Otherwise

Do not stop.

Continue the loop.

---

# Repository Standards

Required:

* Type hints
* Tests
* Logging
* Documentation

Avoid:

* Dead code
* TODOs
* Commented-out code
* Temporary hacks

---

# CI/CD Requirements

Every PR must run:

Lint

Format Check

Unit Tests

Integration Tests

Coverage

Build Validation

---

# Documentation Requirements

Every major feature must update:

Relevant architecture docs.

Relevant roadmap progress.

Relevant Notion tasks.

---

# Definition Of Done

A task is only Done when:

✓ Code complete

✓ Tests passing

✓ PR merged

✓ Notion updated

✓ Main synced

✓ Documentation updated

---

# Mission

Build OpenRabbit into the best open-source self-hosted AI code review platform.

Prioritize:

Quality

Maintainability

Architecture

Developer Experience

Long-term scalability

Every decision should move the repository toward production readiness.
