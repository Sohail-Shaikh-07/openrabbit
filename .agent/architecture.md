# OpenRabbit Architecture

Version: 1.0

Status: Technical Design

Project Type: Open Source

Architecture Style: Local-First Multi-Agent System

---

# Overview

OpenRabbit is a self-hosted AI Pull Request Review platform designed to operate entirely on a user's machine.

The system combines:

* Multi-Agent Review Architecture
* Repository-Aware RAG
* Fine-Tuned Code Review Models
* GitHub Integration
* Local Model Inference

The architecture prioritizes:

* Privacy
* Scalability
* Extensibility
* High Signal Reviews

---

# System Architecture

```text
                         GitHub
                            │
                            ▼
                   GitHub Polling Service
                            │
                            ▼
                    Review Orchestrator
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
 PR Analyzer        Context Retriever      Metadata Store
        │                   │
        ▼                   ▼
 ┌─────────────────────────────────────────────┐
 │                 Agent Layer                 │
 └─────────────────────────────────────────────┘
        │
        ├── Security Agent
        ├── Performance Agent
        ├── Bug Detection Agent
        ├── Architecture Agent
        ├── Test Coverage Agent
        └── Style Agent
        │
        ▼
                Comment Ranking Engine
                            │
                            ▼
                 GitHub Review Publisher
```

---

# High Level Review Flow

```text
PR Detected
    │
    ▼
Diff Extraction
    │
    ▼
Context Retrieval
    │
    ▼
Agent Execution
    │
    ▼
Comment Aggregation
    │
    ▼
Comment Ranking
    │
    ▼
GitHub Review Creation
```

---

# Core Components

## GitHub Polling Service

Responsibilities:

* Detect new PRs
* Detect updated PRs
* Detect new commits
* Trigger review pipeline

Polling Interval:

```yaml
60 seconds
```

Future:

```yaml
webhook mode
```

---

# Review Orchestrator

Central brain of OpenRabbit.

Responsibilities:

* Receive review request
* Load repository context
* Dispatch agents
* Aggregate responses
* Coordinate ranking

Implementation:

```python
LangGraph
```

---

# Repository Scanner

Purpose:

Create repository knowledge base.

Scans:

```text
README.md
docs/
src/
tests/
architecture.md
coding_rules.md
security_rules.md
```

Output:

```text
Chunks
Embeddings
Metadata
```

---

# RAG Architecture

## Why RAG?

Without RAG:

```text
PR Diff
↓
Model
```

Limited understanding.

With RAG:

```text
PR Diff
+
Repository Context
+
Architecture
+
Rules
↓
Model
```

Repository-aware reviews.

---

# Retrieval Sources

## Documentation

README.md

docs/

ADR documents

---

## Rules

coding_rules.md

security_rules.md

review_examples.md

---

## Source Code

Functions

Classes

Modules

---

## Historical Reviews

Previous comments

Previous fixes

Review examples

---

# Chunking Strategy

Documentation:

```yaml
chunk_size: 1000
overlap: 150
```

Source Code:

Chunk by:

* Function
* Class
* Module

Never arbitrary splitting.

---

# Embedding Pipeline

Version 1:

```text
BGE Small
```

Alternative:

```text
Nomic Embed
```

---

# Vector Database

Qdrant

---

# Collections

## functions

Stores:

Functions

Metadata:

```json
{
  "file": "auth.py",
  "function": "login",
  "language": "python"
}
```

---

## classes

Stores:

Classes

Metadata:

```json
{
  "class_name": "UserService"
}
```

---

## docs

Stores:

Documentation

Architecture

README

---

## rules

Stores:

Coding rules

Security rules

Review examples

---

# Retrieval Flow

```text
PR Diff
    │
    ▼
Changed Files
    │
    ▼
Semantic Search
    │
    ▼
Top K Context
    │
    ▼
Agent Input
```

---

# Multi-Agent Architecture

All agents execute in parallel.

---

# Coordinator Agent

Responsibilities:

* Receive review request
* Distribute tasks
* Aggregate findings

Input:

```json
{
  "diff": "...",
  "context": "..."
}
```

---

# Security Agent

Purpose:

Find security issues.

Checks:

* SQL Injection
* XSS
* Secrets
* Authentication
* Authorization

Output:

```json
{
  "severity": "high",
  "category": "security",
  "confidence": 0.92
}
```

---

# Performance Agent

Purpose:

Find performance bottlenecks.

Checks:

* N+1 queries
* Expensive loops
* Memory usage
* Unnecessary allocations

---

# Bug Detection Agent

Purpose:

Find correctness issues.

Checks:

* Logic errors
* Edge cases
* Null handling
* Race conditions

---

# Architecture Agent

Purpose:

Protect architecture integrity.

Checks:

* Layer violations
* Dependency violations
* Design pattern misuse

---

# Test Coverage Agent

Purpose:

Ensure adequate testing.

Checks:

* Missing tests
* Weak assertions
* Coverage gaps

---

# Style Agent

Purpose:

Maintain consistency.

Checks:

* Naming conventions
* Formatting
* Team standards

---

# Agent Communication

```text
Coordinator
    │
    ├── Security
    ├── Performance
    ├── Bug
    ├── Architecture
    ├── Test
    └── Style
```

Agents never communicate directly.

All communication goes through coordinator.

Benefits:

* Simpler architecture
* Easier debugging
* Better observability

---

# Model Layer

## Base Model

Qwen2.5-Coder-7B-Instruct

---

# Fine Tuned Model

OpenRabbit Reviewer V1

Base:

Qwen2.5-Coder-7B

Method:

QLoRA

4-bit

PEFT

---

# Fine Tuning Dataset

Primary:

Zenodo CodeReviewer Dataset

Tasks:

* Review Comment Generation
* Diff Understanding
* Code Review Reasoning

---

# Model Input

```text
PR Diff

Repository Context

Coding Rules

Architecture Rules

Retrieved Examples
```

---

# Model Output

```json
{
  "severity": "high",
  "category": "bug",
  "confidence": 0.91,
  "comment": "...",
  "fix": "..."
}
```

---

# Comment Ranking Engine

Purpose:

Reduce noise.

Input:

```text
50 Findings
```

Output:

```text
5-10 High Quality Findings
```

---

# Ranking Criteria

Confidence

Severity

Novelty

Relevance

Duplicate Detection

---

# GitHub Review Publisher

Responsibilities:

Create:

* Inline Comments
* Review Summary

Uses:

GitHub REST API

---

# Review Lifecycle

```text
PR Opened
    │
    ▼
Review Started
    │
    ▼
Context Retrieved
    │
    ▼
Agents Execute
    │
    ▼
Comments Ranked
    │
    ▼
Review Posted
```

---

# Local Deployment Architecture

```text
User Machine
│
├── OpenRabbit
├── Ollama
├── Qdrant
├── SQLite
└── GitHub Connector
```

No external services required.

---

# Docker Architecture

```yaml
services:
  openrabbit:
  qdrant:
```

Future:

```yaml
postgres:
redis:
```

---

# Storage Architecture

Version 1:

SQLite

Future:

PostgreSQL

---

# Observability

Logging:

```text
Review Started
Agent Executed
Context Retrieved
Review Posted
```

Stored locally.

---

# Security Model

Principles:

* Local execution
* No telemetry
* No code upload
* Encrypted GitHub token storage

---

# Future Architecture

Version 1.1

Auto Fix Suggestions

---

Version 1.2

VS Code Extension

---

Version 2.0

Webhook Mode

Distributed Agents

Enterprise Knowledge Bases

Team Collaboration

---

# Final Architecture Goal

OpenRabbit should behave as a fully self-hosted, repository-aware, multi-agent AI code review platform capable of reviewing pull requests with quality comparable to modern commercial AI review tools while maintaining complete local ownership of source code and infrastructure.
