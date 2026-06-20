# OpenRabbit PRD

## Product Requirements Document

Version: 1.0

Status: Planning

Project Type: Open Source

Target Timeline: 30-40 Days

Repository Name: OpenRabbit

---

# 1. Executive Summary

OpenRabbit is an open-source, self-hosted AI Pull Request Review platform inspired by CodeRabbit.

The goal is to provide automated code reviews directly within GitHub pull requests while running entirely on the user's machine.

Unlike SaaS-based code review products, OpenRabbit does not require a centralized cloud service.

Users install OpenRabbit locally, connect it to their repositories, and receive AI-generated review comments directly inside pull requests.

OpenRabbit combines:

* Multi-Agent Architecture
* Repository-Aware RAG
* Fine-Tuned Code Review Models
* GitHub Integration
* Local Inference
* Automated PR Analysis

---

# 2. Vision

Build the most powerful open-source local-first AI code reviewer.

Users should be able to:

1. Install OpenRabbit
2. Connect GitHub
3. Add configuration files
4. Open a Pull Request
5. Receive high-quality automated reviews

without needing:

* Cloud infrastructure
* SaaS subscriptions
* External dashboards

---

# 3. Core Objectives

## Primary Objectives

### O1

Provide meaningful code review comments.

### O2

Understand repository architecture.

### O3

Review based on repository-specific rules.

### O4

Operate fully on local infrastructure.

### O5

Support multiple programming languages.

### O6

Achieve CodeRabbit-level review quality.

---

# 4. Non Goals

The first version will NOT include:

* IDE plugin
* VS Code extension
* Slack integration
* Jira integration
* Team management
* SaaS hosting
* Billing systems

---

# 5. User Personas

## Solo Developer

Uses OpenRabbit for personal repositories.

### Needs

* Catch bugs
* Improve code quality
* Security suggestions

---

## Open Source Maintainer

Uses OpenRabbit across multiple projects.

### Needs

* Consistent reviews
* Faster pull request processing

---

## Startup Engineering Team

Uses OpenRabbit on self-hosted infrastructure.

### Needs

* Local deployment
* Security
* Private code handling

---

# 6. Product Principles

## Local First

Everything runs on the user machine.

## Privacy First

No code leaves the machine.

## Open Source

All major components are open.

## Repository Aware

Reviews must understand repository context.

## High Signal

Avoid noisy comments.

---

# 7. System Overview

OpenRabbit consists of:

1. GitHub Connector
2. Polling Service
3. Review Orchestrator
4. Multi-Agent System
5. RAG Engine
6. Model Serving Layer
7. Comment Ranking Engine
8. GitHub Comment Publisher

---

# 8. High Level Flow

Developer opens PR

↓

Polling Service detects PR

↓

Repository Context Retrieved

↓

Agents Execute

↓

Review Comments Generated

↓

Comments Ranked

↓

GitHub Review Posted

---

# 9. GitHub Integration

Version 1 uses polling.

Every 60 seconds:

* Check new PRs
* Check updated PRs
* Check new commits

No webhook required.

No public server required.

---

# 10. Repository Configuration

Every repository contains:

.codereviewer/

config.yml

architecture.md

coding_rules.md

security_rules.md

review_examples.md

ignore.txt

---

# 11. Configuration Example

```yaml
review:
  security: true
  performance: true
  architecture: true

model:
  provider: local
  model_name: openrabbit-reviewer-v1

polling:
  interval_seconds: 60
```

# 12. Multi-Agent Architecture

OpenRabbit uses specialized agents.

### PR Analyzer Agent

Responsibilities:

* Analyze diff
* Detect impacted files
* Calculate risk

### Security Agent

Responsibilities:

* Vulnerabilities
* Secrets
* Injection risks

### Performance Agent

Responsibilities:

* Inefficient loops
* Expensive queries
* Memory issues

### Architecture Agent

Responsibilities:

* Layer violations
* Dependency violations

### Bug Detection Agent

Responsibilities:

* Logic bugs
* Edge cases
* Null handling

### Test Coverage Agent

Responsibilities:

* Missing tests
* Weak assertions

### Comment Ranker Agent

Responsibilities:

* Remove duplicates
* Rank usefulness
* Filter noise

# 13. Repository-Aware RAG

OpenRabbit retrieves:

README

Architecture Docs

ADR Documents

Coding Standards

Security Rules

Source Code

Test Files

Historical Reviews

Before review generation.

# 14. Vector Database

Initial Version:

Qdrant

Stored Objects:

Functions

Classes

Architecture Notes

Rules

Review Examples

Documentation

# 15. Model Architecture

Base Model:

Qwen2.5-Coder-7B-Instruct

Fine Tuning:

QLoRA

4-bit Quantization

Training Dataset:

Zenodo CodeReviewer Dataset

Future Datasets:

Filtered GitHub PR Reviews

SWE-PRBench (Evaluation Only)

# 16. Review Generation Pipeline

PR Diff

↓

Context Retrieval

↓

Agent Analysis

↓

Review Generation

↓

Comment Ranking

↓

GitHub Comment Publishing

# 17. Comment Structure

Each comment contains:

Severity

Category

Confidence

Explanation

Suggested Fix

Example:

Severity: High

Category: Security

Confidence: 92%

Comment:

Potential SQL injection risk due to unsanitized user input.

Suggested Fix:

Use parameterized queries.

# 18. Evaluation Metrics

Precision

Recall

False Positive Rate

Review Acceptance Rate

Coverage

Review Quality Score

Target Metrics:

Precision > 70%

False Positive Rate < 10%

# 19. Security Requirements

No code transmission.

No telemetry by default.

Local-only model support.

Encrypted credentials.

GitHub token storage.

# 20. Success Criteria

Users can:

Install OpenRabbit

Connect GitHub

Review PRs automatically

Receive useful comments

Run completely locally

without external services.

# 21. MVP Scope

GitHub Polling

RAG

Qdrant

Qwen2.5-Coder

Security Agent

Bug Detection Agent

Comment Publishing

Local Deployment

# 22. Future Roadmap

Auto Fix PRs

Code Suggestions

VS Code Extension

GitHub App

Distributed Agent Execution

Enterprise Mode

Custom Fine Tuning

Organization Knowledge Bases
