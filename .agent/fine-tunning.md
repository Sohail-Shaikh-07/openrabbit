# OpenRabbit Fine-Tuning Plan

Version: 1.0

Model Name:

OpenRabbit-Reviewer-v1

Base Model:

Qwen2.5-Coder-7B-Instruct

Training Method:

QLoRA

Primary Dataset:

Zenodo CodeReviewer Dataset

Target Timeline:

Days 24-30

---

# Purpose

This document defines how OpenRabbit's review model will be created, trained, evaluated, packaged, and distributed.

The model should specialize in:

* Pull Request Reviews
* Bug Detection
* Security Review
* Architecture Review
* Repository-Aware Code Review
* Review Comment Generation

The model is NOT intended to replace the entire OpenRabbit system.

It is one component within the broader multi-agent architecture.

---

# Model Philosophy

OpenRabbit should not attempt to create a general coding model.

Qwen already solves that.

Instead we specialize Qwen into a review expert.

Goal:

```text id="8u0vzr"
General Coding Model
        ↓
Code Review Specialist
```

---

# Target Behaviors

The model should learn:

## Behavior 1

Detect defects.

Example:

```python id="z74c67"
user = None
user.name
```

Expected:

Identify null dereference risk.

---

## Behavior 2

Review architecture.

Example:

```python id="o2skdi"
controller
    ↓
database
```

Expected:

Detect architecture violation.

---

## Behavior 3

Review security.

Example:

```python id="fch2jw"
cursor.execute(
    f"SELECT * FROM users WHERE id={id}"
)
```

Expected:

Identify SQL injection risk.

---

## Behavior 4

Review tests.

Expected:

Identify missing coverage.

---

## Behavior 5

Generate actionable comments.

Bad:

```text id="yd1r0j"
This code looks wrong.
```

Good:

```text id="hcmkxm"
Potential null pointer exception because
user may be None before dereference.
Consider adding a guard clause.
```

---

# What The Model Should NOT Do

The model should not:

* Rewrite entire files
* Refactor large systems
* Generate documentation
* Generate large code blocks
* Act as a chatbot

Its purpose is:

```text id="r3hwmw"
Review
Analyze
Comment
Suggest
```

---

# Base Model Selection

Chosen Model:

Qwen2.5-Coder-7B-Instruct

Reasons:

* Strong coding performance
* Open source
* Efficient local inference
* Strong Python support
* Strong JavaScript support
* Good reasoning capabilities

Future:

```text id="6fr0zb"
OpenRabbit Reviewer v2
```

may use larger models.

---

# Training Method

Chosen Method:

QLoRA

Benefits:

* Lower VRAM
* Faster training
* Smaller adapters
* Easier distribution

---

# Why Not Full Fine-Tuning

Full fine tuning:

* Expensive
* Large checkpoints
* Difficult distribution

QLoRA provides:

95%+ of the value

with significantly lower cost.

---

# Dataset Strategy

Primary Dataset:

Zenodo CodeReviewer Dataset

Source:

Research dataset for code review generation.

Tasks:

Review Comment Generation

Diff Understanding

Code Review Reasoning

---

# Dataset Usage

We will use:

```text id="93zx4f"
Comment Generation
```

portion of dataset.

Ignore:

Tasks unrelated to review generation.

---

# Dataset Cleaning

Remove:

Duplicate examples

Corrupted examples

Very short reviews

Reviews with no reasoning

---

# Quality Filters

Reject:

```text id="20xtr2"
LGTM
Looks good
Nice work
```

Accept:

```text id="b9hjz4"
Potential race condition
Missing validation
Possible null dereference
```

---

# Data Transformation

Original:

```text id="ps3s2s"
Diff
Review Comment
```

Target Format:

```json id="yzl0v4"
{
  "instruction": "Review the pull request diff.",
  "input": "...diff...",
  "output": "...review comment..."
}
```

---

# Advanced Format

Future Version:

```json id="w61y0m"
{
  "diff": "...",
  "context": "...",
  "severity": "high",
  "category": "security",
  "comment": "...",
  "fix": "..."
}
```

---

# Training Pipeline

Dataset

↓

Cleaning

↓

Formatting

↓

Tokenization

↓

QLoRA Training

↓

Evaluation

↓

Packaging

↓

Release

---

# Tokenization

Tokenizer:

Qwen Tokenizer

Max Context:

8192

Future:

Long context support.

---

# Hyperparameters

Initial Configuration

```yaml id="4c5k0x"
epochs: 2

learning_rate: 2e-4

batch_size: 1

gradient_accumulation: 16

lora_rank: 64

lora_alpha: 16

lora_dropout: 0.05
```

Subject to experimentation.

---

# Training Infrastructure

Preferred:

RunPod RTX 4090

Alternative:

Google Colab T4

---

# Expected Training Duration

QLoRA:

4-12 hours

Depending on:

Dataset size

GPU

Epochs

---

# Evaluation Strategy

Training Set

↓

Validation Set

↓

Benchmark Set

---

# Benchmark Dataset

SWE-PRBench

Evaluation Only

Never used for training.

---

# Evaluation Metrics

## Precision

How many comments are correct.

Target:

70%+

---

## Recall

How many issues are detected.

Target:

60%+

---

## False Positive Rate

Target:

Below 10%

---

## Review Quality

Human assessment.

Target:

Useful actionable comments.

---

# Expected Outputs

The model should produce:

```json id="x95rjt"
{
  "severity": "high",
  "category": "bug",
  "confidence": 0.91,
  "comment": "...",
  "fix": "..."
}
```

---

# Confidence Scoring

Confidence Range:

```text id="iv6j18"
0.00 - 1.00
```

Used later by:

Comment Ranker Agent

---

# Packaging Strategy

Output:

LoRA Adapter

---

# Why LoRA

Size:

```text id="0m4glt"
100MB - 500MB
```

instead of:

```text id="i8ud5s"
15GB+
```

---

# Hugging Face Repository

Publish:

```text id="0h4wzi"
openrabbit/openrabbit-reviewer-v1
```

Contains:

adapter_model.safetensors

adapter_config.json

README.md

---

# User Installation Flow

User runs:

```bash id="7k2h7r"
openrabbit install-model
```

OpenRabbit:

Downloads adapter

Loads base model

Merges adapter

Starts inference

---

# Runtime Architecture

```text id="5l4bxv"
Qwen Base Model
        +
OpenRabbit Adapter
        ↓
OpenRabbit Reviewer
```

---

# Local Inference Support

Supported:

Ollama

Transformers

vLLM

---

# Model Registry

Future Models

```text id="5az01w"
openrabbit-reviewer-v1

openrabbit-reviewer-v1.1

openrabbit-reviewer-v2
```

---

# Release Criteria

The model is ready when:

✓ Training completes

✓ Validation passes

✓ Benchmark passes

✓ Adapter exported

✓ Hugging Face repository published

✓ Installation tested

✓ OpenRabbit integration complete

---

# Success Definition

OpenRabbit Reviewer V1 should generate significantly better pull request review comments than the base Qwen2.5-Coder model while remaining lightweight enough for local deployment and open-source distribution.

The model should function as the review intelligence layer powering OpenRabbit's multi-agent review pipeline.
