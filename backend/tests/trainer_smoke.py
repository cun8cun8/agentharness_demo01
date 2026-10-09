"""Run inside the trainer image with networking disabled."""
import json
import runpy
import shutil
from pathlib import Path

import torch
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast

torch.set_num_threads(1)
vocab = {token: i for i, token in enumerate(["[PAD]", "[UNK]", "[EOS]", "hello", "world", "good", "bad", "patch"])}
backend = Tokenizer(WordLevel(vocab, unk_token="[UNK]"))
backend.pre_tokenizer = Whitespace()
tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, pad_token="[PAD]", unk_token="[UNK]", eos_token="[EOS]")
tokenizer.save_pretrained("/model")
model = GPT2LMHeadModel(GPT2Config(vocab_size=len(vocab), n_positions=2048, n_embd=16, n_layer=1, n_head=2, bos_token_id=2, eos_token_id=2, pad_token_id=0))
model.save_pretrained("/model")
Path("/input").mkdir(exist_ok=True)
Path("/output").mkdir(exist_ok=True)
rows = {
    "sft": {"text": "hello world good patch [EOS]"},
    "dpo": {"prompt": "hello world ", "chosen": "good patch [EOS]", "rejected": "bad patch [EOS]"},
    "grpo": {"prompt": "hello world", "reference_patch": "good patch"},
}
for method, row in rows.items():
    Path("/input/dataset.jsonl").write_text("\n".join(json.dumps(row) for _ in range(4)) + "\n", encoding="utf-8")
    Path("/input/config.json").write_text(json.dumps({"method": method, "max_seconds": 180, "max_steps": 1, "learning_rate": 0.00001}), encoding="utf-8")
    runpy.run_path("/app/train.py", run_name="__main__")
    assert Path("/output/model/config.json").is_file(), method
    metrics = json.loads(Path("/output/metrics.json").read_text(encoding="utf-8"))
    assert "train_loss" in metrics, (method, metrics)
    print(f"PASS: {method} trained offline and saved model and metrics", flush=True)
    for path in Path("/output").iterdir():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
