# OpenRabbit Outcome Document

Version: 1.0

Status: Target End State

Project Type: Open Source

Timeline: 30-40 Days

---

# Purpose

This document defines exactly what OpenRabbit should look like when Version 1.0 is complete.

The goal is to remove ambiguity and provide a concrete vision for contributors and maintainers.

---

# Product Vision

OpenRabbit should become the open-source alternative to CodeRabbit.

A developer should be able to:

```bash
pip install openrabbit
```

or

```bash
docker compose up
```

connect a GitHub repository and immediately receive automated pull request reviews.

The entire system should run locally.

No cloud account.

No SaaS.

No subscription.

No code leaves the machine.

---

# Final User Experience

## Installation

User runs:

```bash
pip install openrabbit
```

Initialize:

```bash
openrabbit init
```

OpenRabbit creates:

```text
.codereviewer/
├── config.yml
├── architecture.md
├── coding_rules.md
├── security_rules.md
├── review_examples.md
└── ignore.txt
```

---

# Repository Setup

Developer fills:

## architecture.md

Contains:

* System architecture
* Services
* Layers
* Design decisions

---

## coding_rules.md

Contains:

* Naming conventions
* Design patterns
* Team standards

---

## security_rules.md

Contains:

* Security requirements
* Authentication rules
* Encryption requirements

---

# Running OpenRabbit

Start OpenRabbit:

```bash
openrabbit start
```

OpenRabbit starts:

```text
GitHub Polling Service
Qdrant
Review Agents
Model Runtime
```

---

# Pull Request Flow

Developer creates PR.

OpenRabbit automatically detects:

```text
PR #142
```

OpenRabbit starts review.

No user interaction required.

---

# Review Pipeline

PR

↓

Diff Analysis

↓

Context Retrieval

↓

Agent Execution

↓

Review Generation

↓

Comment Ranking

↓

GitHub Review Posting

---

# Example Review

OpenRabbit comments:

```text
HIGH RISK

Potential SQL Injection

File:
auth/user_repository.py

Reason:
User input reaches database query without sanitization.

Suggested Fix:
Use parameterized queries.
```

Posted directly inside GitHub.

---

# User Interface Philosophy

Version 1:

No dashboard required.

Everything works automatically.

CLI-first.

---

# CLI Commands

## Initialize

```bash
openrabbit init
```

## Start

```bash
openrabbit start
```

## Stop

```bash
openrabbit stop
```

## Reindex Repository

```bash
openrabbit index
```

## Review Specific PR

```bash
openrabbit review --pr 142
```

---

# Repository Structure

```text
openrabbit/

├── agents/
├── rag/
├── github/
├── ranking/
├── models/
├── finetuning/
├── cli/
├── api/
├── configs/
├── tests/
├── docs/
└── examples/
```

---

# Agent System

OpenRabbit should contain:

## PR Analyzer Agent

Responsibilities:

* Parse diff
* Determine risk

---

## Security Agent

Responsibilities:

* Injection detection
* Secret exposure
* Auth vulnerabilities

---

## Performance Agent

Responsibilities:

* Expensive operations
* Memory issues
* Query inefficiencies

---

## Bug Detection Agent

Responsibilities:

* Logic errors
* Null handling
* Edge cases

---

## Architecture Agent

Responsibilities:

* Layer violations
* Dependency violations

---

## Test Agent

Responsibilities:

* Missing tests
* Poor coverage

---

## Comment Ranker Agent

Responsibilities:

* Remove noise
* Merge duplicates
* Prioritize findings

---

# Agent Collaboration

All agents run in parallel.

Coordinator receives:

```text
PR Diff
Repository Context
Retrieved Knowledge
```

Coordinator dispatches work.

Agents return findings.

Comment Ranker merges results.

Final review is generated.

---

# RAG Requirements

OpenRabbit must understand:

Repository

Architecture

Standards

Documentation

Previous Reviews

Tests

---

# Indexed Sources

README.md

docs/

architecture.md

coding_rules.md

security_rules.md

tests/

src/

Historical Reviews

---

# Knowledge Retrieval

Before every review:

Relevant files retrieved.

Relevant architecture sections retrieved.

Relevant coding rules retrieved.

Relevant review examples retrieved.

Only then is the model invoked.

---

# Model Requirements

Version 1:

Qwen2.5-Coder-7B-Instruct

---

# Fine Tuned Model

OpenRabbit Reviewer v1

Based On:

Qwen2.5-Coder-7B

Training Method:

QLoRA

4-bit

---

# Training Data

Primary:

Zenodo CodeReviewer Dataset

Secondary:

Filtered GitHub Review Comments

---

# Evaluation Data

SWE-PRBench

Used only for evaluation.

Never for training.

---

# Local Inference

Supported:

Ollama

vLLM

Transformers

---

# Storage Components

Vector Database:

Qdrant

---

Metadata Database:

SQLite (V1)

PostgreSQL (Future)

---

# Feature Parity Goals

| Feature              | OpenRabbit |
| -------------------- | ---------- |
| PR Reviews           | Yes        |
| Inline Comments      | Yes        |
| Repository Awareness | Yes        |
| Security Review      | Yes        |
| Performance Review   | Yes        |
| Architecture Review  | Yes        |
| Bug Detection        | Yes        |
| RAG                  | Yes        |
| Local Execution      | Yes        |
| Open Source          | Yes        |
| Fine Tuned Model     | Yes        |
| GitHub Integration   | Yes        |
| Auto Fix Suggestions | Planned    |
| VS Code Extension    | Planned    |

---

# Definition of Success

A user should be able to:

Install OpenRabbit

Connect GitHub

Open PR

Receive meaningful AI review

Without:

Cloud Services

External APIs

Centralized Infrastructure

Manual Review Triggers

---

# Release Criteria

Version 1.0 is complete when:

✓ GitHub polling works

✓ PR review works

✓ RAG works

✓ Multi-agent review works

✓ Fine-tuned model works

✓ Local deployment works

✓ Documentation complete

✓ Open-source repository published

---

# Final Outcome

OpenRabbit becomes:

"The self-hosted, open-source CodeRabbit alternative powered by multi-agent AI and repository-aware code review."
