# Training OpenRabbit-Reviewer-v1

This directory contains the standalone training entry point for the
OpenRabbit fine-tuned code review model based on Qwen2.5-Coder-7B-Instruct.

---

## Local validation (CPU, no GPU needed)

Run this on any machine to confirm the script and config are valid before
paying for cloud GPU time:

```bash
python scripts/train.py --config configs/training.yml --mock
```

The `--mock` flag skips model loading and training. It validates config
parsing and prints what a real run would do. Expected output:

```
[mock] Config loaded: model=Qwen/Qwen2.5-Coder-7B-Instruct
[mock] LoRA: rank=16  alpha=32  dropout=0.05
[mock] Training: epochs=2  lr=0.0002  batch=16
[mock] Output dir: outputs/openrabbit-reviewer-v1
[mock] Dataset: 2 synthetic rows
[mock] Skipping prepare() and train() -- no GPU required
```

---

## RunPod RTX 4090

### 1. Launch pod

- Template: **RunPod PyTorch 2.x** (CUDA 12.1+)
- GPU: **RTX 4090** (24 GB VRAM)
- Disk: 50 GB minimum (dataset + model cache)

### 2. Clone and install

```bash
git clone https://github.com/Sohail-Shaikh-07/openrabbit
cd openrabbit
pip install "poetry==2.4.1"
poetry config virtualenvs.create false
poetry install --with finetuning --no-interaction --no-ansi
```

### 3. Download the dataset

```bash
mkdir -p dataset/Comment_Generation
# Download msg-train.jsonl from the Zenodo CodeReviewer dataset
# and place it at dataset/Comment_Generation/msg-train.jsonl
```

### 4. Validate before training

```bash
python scripts/train.py --config configs/training.yml --mock
```

### 5. Start training

```bash
python scripts/train.py \
    --config configs/training.yml \
    --data dataset/Comment_Generation/msg-train.jsonl
```

Expected duration: 4-8 hours for 2 epochs on ~117K cleaned examples.

### 6. Retrieve the adapter

The LoRA adapter is saved to `outputs/openrabbit-reviewer-v1/` after training.
Copy it off the pod before it expires:

```bash
# From your local machine
rsync -avz root@<pod-ip>:/workspace/openrabbit/outputs ./outputs
```

---

## Google Colab T4

### 1. Open a new notebook with a T4 GPU runtime

Runtime > Change runtime type > T4 GPU

### 2. Clone and install

```python
!git clone https://github.com/Sohail-Shaikh-07/openrabbit
%cd openrabbit
!pip install "poetry==2.4.1"
!poetry config virtualenvs.create false
!poetry install --with finetuning --no-interaction --no-ansi
```

### 3. Upload the dataset

```python
from google.colab import files
uploaded = files.upload()   # upload msg-train.jsonl
!mkdir -p dataset/Comment_Generation
!mv msg-train.jsonl dataset/Comment_Generation/
```

### 4. Validate

```python
!python scripts/train.py --config configs/training.yml --mock
```

### 5. Train

```python
!python scripts/train.py \
    --config configs/training.yml \
    --data dataset/Comment_Generation/msg-train.jsonl
```

Expected duration: 8-12 hours for 2 epochs on T4 (15 GB VRAM).

> **T4 note**: The default config uses `bf16: true`, which is not supported
> on T4. Override before running:
>
> ```bash
> # Edit configs/training.yml: set bf16: false and fp16: true
> ```
>
> Or pass a custom config:
>
> ```yaml
> # configs/training_t4.yml
> bf16: false
> fp16: true
> per_device_train_batch_size: 1
> gradient_accumulation_steps: 16
> ```

### 6. Save the adapter to Google Drive

```python
from google.colab import drive
drive.mount("/content/drive")
!cp -r outputs/openrabbit-reviewer-v1 "/content/drive/MyDrive/"
```

---

## Config reference

All hyperparameters are in `configs/training.yml`. Key fields:

| Field | Default | Notes |
|---|---|---|
| `model_name` | `Qwen/Qwen2.5-Coder-7B-Instruct` | Base model |
| `lora_r` | `16` | LoRA rank |
| `lora_alpha` | `32` | LoRA scaling (2x rank) |
| `num_train_epochs` | `2` | Epochs |
| `learning_rate` | `2e-4` | Initial LR |
| `per_device_train_batch_size` | `2` | Per-GPU batch |
| `gradient_accumulation_steps` | `8` | Effective batch = 16 |
| `bf16` | `true` | Use `false` + `fp16: true` on T4 |
| `output_dir` | `outputs/openrabbit-reviewer-v1` | Adapter output path |

Full field list: see `src/finetuning/config.py`.
