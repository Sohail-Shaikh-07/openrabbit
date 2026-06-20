# OpenRabbit Agent Specifications

Version: 1.0

Status: Production Design

Architecture:

Multi-Agent Parallel Review System

Framework:

LangGraph

Primary Model:

OpenRabbit-Reviewer-v1

Base Model:

Qwen2.5-Coder-7B-Instruct

---

# Purpose

This document defines:

* Agent responsibilities
* Inputs
* Outputs
* Confidence scoring
* Communication rules
* Execution flow
* Prompt contracts
* Failure handling

Every agent must have a single responsibility.

Agents should not overlap excessively.

Agents should work independently and in parallel.

---

# Design Philosophy

Bad:

```text
One Giant Agent
```

Good:

```text
Many Specialized Agents
```

Benefits:

* Better reasoning
* Better scalability
* Easier debugging
* Better evaluation

---

# Agent Hierarchy

```text
Coordinator Agent
        │
        ├── PR Analyzer Agent
        ├── Security Agent
        ├── Performance Agent
        ├── Bug Detection Agent
        ├── Architecture Agent
        ├── Test Coverage Agent
        ├── Style Agent
        └── Context Agent

                ↓

        Comment Ranker Agent

                ↓

        GitHub Publisher Agent
```

---

# Universal Agent Contract

Every agent receives:

```json
{
  "pr_diff": "...",
  "repository_context": "...",
  "retrieved_context": "...",
  "rules": "...",
  "metadata": {}
}
```

Every agent returns:

```json
{
  "agent": "security",
  "findings": [],
  "confidence": 0.92,
  "execution_time": 2.1
}
```

---

# Finding Format

Every finding must follow:

```json
{
  "severity": "high",
  "category": "security",
  "file": "auth.py",
  "line": 123,
  "confidence": 0.91,
  "title": "SQL Injection Risk",
  "reason": "...",
  "suggestion": "...",
  "fix": "..."
}
```

---

# Severity Levels

## Critical

Immediate production risk

Examples:

* Secret exposure
* Auth bypass
* Remote execution

---

## High

Likely bug or vulnerability

Examples:

* SQL Injection
* Race conditions

---

## Medium

Quality issue

Examples:

* Missing validation
* Missing tests

---

## Low

Improvement suggestion

Examples:

* Naming
* Refactoring

---

# Coordinator Agent

## Purpose

Central orchestration layer.

---

## Responsibilities

Receive PR review request

Load repository context

Trigger retrieval

Dispatch agents

Aggregate results

Handle failures

---

## Input

```json
{
  "pr_id": 123
}
```

---

## Output

```json
{
  "review_id": "...",
  "findings": []
}
```

---

## Must Never

Review code itself.

It only coordinates.

---

# PR Analyzer Agent

## Purpose

Understand the pull request.

---

## Responsibilities

Analyze:

Files changed

Functions changed

Classes changed

Risk score

Complexity score

---

## Output

```json
{
  "risk": "high",
  "files_changed": 12,
  "functions_changed": 18
}
```

---

## Questions It Answers

What changed?

Where are risks located?

What should other agents inspect?

---

# Context Agent

## Purpose

Repository understanding.

---

## Responsibilities

Retrieve:

Architecture

Rules

Examples

Historical reviews

Related files

Documentation

---

## Output

```json
{
  "retrieved_context": [...]
}
```

---

## Never

Generate findings.

Only provide context.

---

# Security Agent

## Purpose

Detect security issues.

---

## Checks

Authentication

Authorization

Secrets

SQL Injection

XSS

CSRF

Token Leakage

Unsafe Deserialization

Path Traversal

SSRF

---

## Input

PR Diff

Retrieved Security Context

---

## Output

Security Findings

---

## Example

```python
cursor.execute(
 f"SELECT * FROM users WHERE id={id}"
)
```

Expected:

High severity finding.

---

# Security Confidence Rules

Critical:

0.90+

High:

0.80+

Medium:

0.70+

Below:

Discard

---

# Performance Agent

## Purpose

Detect performance issues.

---

## Checks

N+1 Queries

Inefficient Loops

Repeated Computation

Memory Usage

Database Performance

Large Allocations

Blocking Operations

---

## Example

```python
for user in users:
    User.objects.get(id=user.id)
```

Expected:

N+1 Query Detection.

---

# Bug Detection Agent

## Purpose

Detect correctness issues.

---

## Checks

Null Dereference

Logic Errors

Boundary Conditions

Race Conditions

Concurrency

Error Handling

Exception Safety

---

## Example

```python
user.name
```

when:

```python
user = None
```

Expected:

High severity finding.

---

# Architecture Agent

## Purpose

Protect repository architecture.

---

## Sources

architecture.md

docs/

ADRs

---

## Checks

Layer Violations

Dependency Violations

Service Boundaries

Domain Rules

Architecture Drift

---

## Example

```text
Controller
    ↓
Database
```

Skipping service layer.

Expected:

Architecture violation.

---

# Test Coverage Agent

## Purpose

Review testing quality.

---

## Checks

Missing Tests

Weak Assertions

Untested Logic

Critical Paths

Regression Risk

---

## Example

New feature added.

No test changes.

Expected:

Medium severity finding.

---

# Style Agent

## Purpose

Consistency.

---

## Checks

Naming

Formatting

Conventions

Code Organization

---

## Severity

Only:

Low

Medium

Never:

Critical

---

# Comment Ranker Agent

## Purpose

Noise reduction.

---

# Input

All findings.

Example:

```text
50 findings
```

---

# Output

```text
5-10 findings
```

---

# Ranking Factors

Confidence

Severity

Novelty

Actionability

Duplication

Repository Relevance

---

# Deduplication Rules

Merge:

```text
Possible SQL injection

Unsafe query execution
```

into:

```text
Single Security Finding
```

---

# Confidence Calculation

Formula:

```text
Model Confidence
+
Retrieval Confidence
+
Agent Confidence
```

Normalized:

0-1

---

# GitHub Publisher Agent

## Purpose

Publish final review.

---

## Responsibilities

Create:

Inline Comments

Review Summary

---

## Format

```text
HIGH

Potential SQL Injection

Reason:
...

Suggested Fix:
...
```

---

# Agent Communication Rules

Rule 1

Agents never communicate directly.

---

Rule 2

Coordinator is the only router.

---

Rule 3

Agents must be stateless.

---

Rule 4

Agents must be independently testable.

---

# Failure Handling

If one agent fails:

```text
Security Agent
     X
```

System continues.

Other agents finish.

Review still produced.

---

# Execution Strategy

Parallel Execution

```text
Security
Performance
Bug
Architecture
Test
Style

All Run Together
```

---

# Time Budget

Target:

Per Agent

```text
5-15 seconds
```

---

Total Review:

```text
30-60 seconds
```

for average PR.

---

# Future Agents

Version 1.1

Fix Suggestion Agent

---

Version 1.2

Documentation Agent

---

Version 1.3

Refactoring Agent

---

Version 2.0

Learning Agent

---

# Agent Success Criteria

Each agent should:

Produce useful findings

Avoid hallucinations

Use repository context

Provide actionable fixes

Generate confidence scores

Remain independently testable

Contribute meaningful signal to the final review

without overwhelming developers with noise.
