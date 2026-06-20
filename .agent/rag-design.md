# OpenRabbit RAG Design

Version: 1.0

Status: Production Design

Component:

Repository-Aware Retrieval Augmented Generation (RAG)

Priority:

Critical

---

# Executive Summary

RAG is the most important component of OpenRabbit.

The quality difference between:

```text
PR Diff
↓
LLM
```

and

```text
PR Diff
+
Repository Context
+
Architecture
+
Rules
+
Documentation
↓
LLM
```

is massive.

The fine-tuned model improves review quality.

The RAG system enables repository understanding.

Without RAG:

OpenRabbit behaves like a generic code reviewer.

With RAG:

OpenRabbit behaves like a repository-aware senior engineer.

---

# Core Goal

Before reviewing a PR, OpenRabbit should understand:

* Repository architecture
* Coding standards
* Security standards
* Related files
* Similar implementations
* Historical review patterns
* Existing tests

The reviewer should never review code in isolation.

---

# Design Principles

## Principle 1

Repository understanding first.

Model invocation second.

---

## Principle 2

Retrieve only relevant context.

Never dump the entire repository.

---

## Principle 3

Code chunks and document chunks are different.

---

## Principle 4

Architecture and rules are first-class knowledge.

---

## Principle 5

Retrieval quality is more important than retrieval quantity.

---

# RAG Architecture

```text
Repository
      │
      ▼
Repository Scanner
      │
      ▼
Chunking Engine
      │
      ▼
Embedding Engine
      │
      ▼
Qdrant
      │
      ▼
Retriever
      │
      ▼
Context Builder
      │
      ▼
Review Agents
```

---

# Repository Sources

OpenRabbit indexes:

```text
README.md

docs/

architecture.md

coding_rules.md

security_rules.md

review_examples.md

src/

tests/

ADRs

Historical Reviews
```

---

# Knowledge Categories

## Architecture Knowledge

Purpose:

Understand system design.

Examples:

```text
Microservices

CQRS

Clean Architecture

Hexagonal Architecture
```

---

## Rule Knowledge

Purpose:

Understand repository expectations.

Examples:

```text
Naming conventions

Code standards

Security standards
```

---

## Source Code Knowledge

Purpose:

Understand implementation.

Examples:

```text
Functions

Classes

Modules
```

---

## Test Knowledge

Purpose:

Understand validation patterns.

Examples:

```text
Unit tests

Integration tests

Regression tests
```

---

## Review Knowledge

Purpose:

Learn repository review style.

Examples:

```text
Past review comments

Review examples
```

---

# Repository Scanner

Purpose:

Continuously index repository knowledge.

---

# Initial Scan

Runs during:

```bash
openrabbit init
```

---

# Reindexing

Runs:

```bash
openrabbit index
```

---

# Incremental Updates

Runs:

```text
Only changed files
```

after repository updates.

---

# Chunking Strategy

Different content types require different chunking.

---

# Documentation Chunking

Files:

README

docs

architecture

rules

---

Settings:

```yaml
chunk_size: 1000

overlap: 150
```

---

# Function Chunking

Each function becomes:

One chunk

---

Example:

```python
def login():
```

↓

One vector record.

---

# Class Chunking

Each class becomes:

One chunk

---

Example:

```python
class UserService:
```

↓

One vector record.

---

# Test Chunking

Each test suite becomes:

One chunk.

---

# Why Not Fixed Chunking

Bad:

```text
Split every 1000 characters
```

Breaks code semantics.

---

Good:

```text
Chunk by AST structures
```

Preserves meaning.

---

# Language Support

Version 1

Python

JavaScript

TypeScript

---

Version 1.1

Java

Go

Rust

C#

---

# Parsing Strategy

Use Tree-Sitter.

Benefits:

* Accurate
* Multi-language
* AST aware

---

# Embedding Model

Version 1

BGE Small

---

Alternative

Nomic Embed

---

Requirements

Fast

Local

Open source

Small footprint

---

# Vector Database

Chosen:

Qdrant

Reasons:

* Local deployment
* Fast retrieval
* Metadata filtering
* Open source

---

# Collections

OpenRabbit uses multiple collections.

---

# Collection: functions

Purpose:

Store functions.

---

Metadata:

```json
{
  "file": "auth.py",
  "function": "login",
  "language": "python"
}
```

---

# Collection: classes

Purpose:

Store classes.

---

Metadata:

```json
{
  "class_name": "UserService",
  "file": "user_service.py"
}
```

---

# Collection: docs

Purpose:

Store documentation.

---

Metadata:

```json
{
  "source": "architecture.md",
  "section": "Authentication"
}
```

---

# Collection: rules

Purpose:

Store repository rules.

---

Metadata:

```json
{
  "rule_type": "security"
}
```

---

# Collection: reviews

Purpose:

Store review examples.

---

Metadata:

```json
{
  "severity": "high"
}
```

---

# Retrieval Pipeline

Step 1

Receive PR.

---

Step 2

Extract changed files.

---

Step 3

Extract changed functions.

---

Step 4

Generate retrieval queries.

---

Step 5

Search Qdrant.

---

Step 6

Collect top-k results.

---

Step 7

Build context package.

---

Step 8

Send to agents.

---

# Retrieval Sources Per Agent

Different agents receive different context.

---

# Security Agent

Retrieves:

security_rules.md

authentication docs

security examples

---

# Architecture Agent

Retrieves:

architecture.md

ADRs

service boundaries

---

# Performance Agent

Retrieves:

performance guidelines

related implementations

---

# Test Agent

Retrieves:

existing tests

test conventions

---

# Context Builder

Purpose:

Build agent-specific context.

---

Example

Security Agent receives:

```text
PR Diff

Security Rules

Related Functions

Authentication Docs
```

Not:

Entire repository.

---

# Top-K Strategy

Version 1

```yaml
top_k: 10
```

---

Future

Adaptive retrieval.

---

# Context Compression

Problem:

Too much retrieved context.

---

Solution:

Context summarization.

---

Example

50 documents

↓

10 summaries

↓

Agent input

---

# Similar Function Retrieval

Purpose:

Find existing implementations.

Example:

PR introduces:

```python
def login():
```

Retriever finds:

```python
def authenticate():
```

Model compares patterns.

---

# Historical Review Retrieval

Purpose:

Review consistency.

Retrieve:

Previous comments

Previous fixes

Accepted reviews

---

# Repository Rules Retrieval

Always injected.

High priority.

Never skipped.

---

Example

```text
All database access must go through repositories.
```

Used by Architecture Agent.

---

# Retrieval Quality Metrics

Measure:

Recall@K

MRR

Context Relevance

Agent Satisfaction Score

---

# Caching Strategy

Cache:

Frequently retrieved documents.

---

Benefits:

Faster reviews.

Lower latency.

---

# Failure Handling

If retrieval fails:

Fallback:

```text
PR Diff Only
```

Review still runs.

---

# Security Considerations

All embeddings remain local.

No repository content leaves machine.

No cloud vector database.

No telemetry.

---

# Future Enhancements

Version 1.1

Cross-repository retrieval.

---

Version 1.2

Semantic code graph.

---

Version 2.0

Knowledge graph retrieval.

---

Version 2.0

Repository memory system.

---

# Success Criteria

The RAG system is successful when:

✓ Relevant architecture context retrieved

✓ Relevant rules retrieved

✓ Relevant code retrieved

✓ Relevant tests retrieved

✓ Agents receive focused context

✓ Review quality improves significantly

✓ Retrieval latency remains under 2 seconds

✓ Everything operates locally

---

# Final Objective

OpenRabbit should understand a repository before reviewing it.

The model should review code like a developer who has already spent weeks learning the codebase rather than a generic LLM seeing the repository for the first time.
