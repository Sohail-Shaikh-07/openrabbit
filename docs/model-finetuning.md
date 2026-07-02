# Fine-Tuning And Local Model Usage

This guide explains how OpenRabbit trains `OpenRabbit-Reviewer-v1`, where the files go, and how the local review command uses the model.

## Current Runtime Path

OpenRabbit reviews run against a local Ollama model.

The configured model name comes from `.openrabbit/config.yml`:

```yaml
model:
  provider: ollama
  model_name: openrabbit-reviewer-v1
  base_model: qwen2.5-coder:7b
```

`openrabbit review --pr <number>` fetches the PR, runs the enabled agents against the local Ollama model, ranks the findings, and prints them. It does not post comments to GitHub yet.

## Models

Training base model:

```text
Qwen/Qwen2.5-Coder-7B-Instruct
```

Default local Ollama base model:

```text
qwen2.5-coder:7b
```

Expected fine-tuned Ollama model name:

```text
openrabbit-reviewer-v1
```

## Dataset

OpenRabbit uses the Zenodo CodeReviewer dataset, specifically the comment generation split:

```text
dataset/Comment_Generation/msg-train.jsonl
```

Used fields:

```text
patch  -> pull request diff hunk
msg    -> target review comment
oldf   -> loaded for completeness, not used in the prompt
```

The dataset is intentionally not committed to git.

## Google Colab Free Tier

Free Colab runtimes are interruptible. Save outputs to Google Drive before the session ends.

### 1. Start Colab

1. Open https://colab.research.google.com/.
2. Create a new notebook.
3. Go to `Runtime` -> `Change runtime type`.
4. Select `T4 GPU`.
5. Click `Save`.

Verify the GPU:

```python
!nvidia-smi
```

### 2. Clone OpenRabbit

```python
!git clone https://github.com/Sohail-Shaikh-07/openrabbit.git
%cd openrabbit
```

### 3. Install Dependencies

```python
!pip install "poetry==2.4.1"
!poetry config virtualenvs.create false
!poetry install --with finetuning --no-interaction --no-ansi
```

### 4. Add The Dataset

Upload `msg-train.jsonl` from the CodeReviewer `Comment_Generation` split:

```python
from google.colab import files

uploaded = files.upload()
```

Move it into the expected path:

```python
!mkdir -p dataset/Comment_Generation
!mv msg-train.jsonl dataset/Comment_Generation/msg-train.jsonl
```

### 5. Create A T4 Config

T4 does not support bf16. Use fp16 and a smaller per-device batch:

```python
%%writefile configs/training_t4.yml
model_name: "Qwen/Qwen2.5-Coder-7B-Instruct"
lora_r: 16
lora_alpha: 32
lora_dropout: 0.05
lora_bias: "none"
lora_target_modules:
  - q_proj
  - k_proj
  - v_proj
  - o_proj
  - gate_proj
  - up_proj
  - down_proj
load_in_4bit: true
bnb_4bit_quant_type: "nf4"
bnb_4bit_compute_dtype: "float16"
bnb_4bit_use_double_quant: true
num_train_epochs: 2
per_device_train_batch_size: 1
gradient_accumulation_steps: 16
learning_rate: 2.0e-4
warmup_ratio: 0.03
lr_scheduler_type: "cosine"
weight_decay: 0.01
optim: "paged_adamw_8bit"
max_seq_length: 2048
bf16: false
fp16: true
gradient_checkpointing: true
output_dir: "outputs/openrabbit-reviewer-v1"
logging_steps: 10
save_steps: 200
save_total_limit: 3
seed: 42
```

### 6. Validate The Script

```python
!python scripts/train.py --config configs/training_t4.yml --mock
```

### 7. Train

```python
!python scripts/train.py \
  --config configs/training_t4.yml \
  --data dataset/Comment_Generation/msg-train.jsonl
```

Expected output directory:

```text
outputs/openrabbit-reviewer-v1/
```

Important files:

```text
adapter_model.safetensors
adapter_config.json
tokenizer.json
tokenizer_config.json
```

### 8. Save To Google Drive

```python
from google.colab import drive

drive.mount("/content/drive")
!mkdir -p "/content/drive/MyDrive/openrabbit"
!cp -r outputs/openrabbit-reviewer-v1 "/content/drive/MyDrive/openrabbit/"
```

## Package The Adapter

After training, package the adapter:

```python
!python - <<'PY'
from pathlib import Path
from finetuning.packager import AdapterPackager

info = AdapterPackager("openrabbit/openrabbit-reviewer-v1").save(
    source_dir=Path("outputs/openrabbit-reviewer-v1"),
    output_dir=Path("release/openrabbit-reviewer-v1"),
)
print(info.output_dir)
for path in info.files:
    print(path)
PY
```

To upload to Hugging Face:

```python
import os
from pathlib import Path
from getpass import getpass
from finetuning.packager import AdapterPackager

token = getpass("HF token: ")
url = AdapterPackager("your-hf-user/openrabbit-reviewer-v1").upload(
    Path("release/openrabbit-reviewer-v1"),
    token=token,
)
print(url)
```

## Use Locally With Ollama

OpenRabbit talks to Ollama by model name. It does not load PEFT adapters directly.

First install and test the base model:

```bash
ollama pull qwen2.5-coder:7b
ollama run qwen2.5-coder:7b
```

If you only want to use the base model, set:

```yaml
model:
  provider: ollama
  model_name: qwen2.5-coder:7b
```

To use the fine-tuned model, import or convert your trained adapter into Ollama and name the final Ollama model:

```text
openrabbit-reviewer-v1
```

Ollama's Modelfile supports `ADAPTER` for fine-tuned LoRA adapters. The base model in `FROM` must match the model used for fine-tuning.

Example `Modelfile`:

```text
FROM qwen2.5-coder:7b
ADAPTER /absolute/path/to/openrabbit-reviewer-v1
PARAMETER temperature 0.1
PARAMETER num_ctx 8192
SYSTEM You are OpenRabbit, a senior pull request reviewer. Return concise JSON findings only.
```

Create the Ollama model:

```bash
ollama create openrabbit-reviewer-v1 -f Modelfile
ollama run openrabbit-reviewer-v1
```

If Ollama rejects the safetensors adapter for the base architecture, merge or convert the adapter to a supported GGUF model or GGUF adapter first, then create the Ollama model from that artifact. OpenRabbit only needs the final Ollama model name to exist.

## Configure OpenRabbit

In the repository you want reviewed:

```bash
poetry run openrabbit init
```

Edit `.openrabbit/config.yml`:

```yaml
github:
  token_env: GITHUB_TOKEN

repository:
  target: owner/repo

model:
  provider: ollama
  model_name: openrabbit-reviewer-v1
  base_model: qwen2.5-coder:7b

review:
  security: true
  performance: true
  architecture: true
  bug: true
  test_coverage: true
```

Run a local dry-run review:

```bash
set GITHUB_TOKEN=ghp_your_token_here
ollama serve
poetry run openrabbit review --pr 123 --repo owner/repo --dry-run
```

The command prints ranked model findings. GitHub comment publishing is intentionally still disabled in this path.
