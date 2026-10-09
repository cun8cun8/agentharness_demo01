"""Offline SFT/DPO runner. Input and base model mounts are read-only."""
import json
import os
import signal
import re
from pathlib import Path

os.environ.update(HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1", HF_HOME="/tmp/huggingface", WANDB_DISABLED="true", TOKENIZERS_PARALLELISM="false")


def main():
    config = json.loads(Path("/input/config.json").read_text())
    signal.alarm(config["max_seconds"])
    import torch
    from datasets import load_dataset
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import DPOConfig, DPOTrainer, GRPOConfig, GRPOTrainer, SFTConfig, SFTTrainer
    tokenizer = AutoTokenizer.from_pretrained("/model", local_files_only=True, trust_remote_code=False)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained("/model", local_files_only=True, trust_remote_code=False, use_safetensors=True)
    dataset = load_dataset("json", data_files="/input/dataset.jsonl", split="train", cache_dir="/tmp/datasets")
    common = dict(output_dir="/output/checkpoints", max_steps=config["max_steps"], learning_rate=config["learning_rate"],
                  per_device_train_batch_size=1, gradient_accumulation_steps=1, report_to="none", logging_steps=1,
                  save_steps=50, save_total_limit=2, use_cpu=not torch.cuda.is_available(), bf16=False, seed=42)
    if config["method"] == "sft":
        trainer = SFTTrainer(model=model, processing_class=tokenizer, train_dataset=dataset, args=SFTConfig(**common, max_length=1024))
    elif config["method"] == "dpo":
        trainer = DPOTrainer(model=model, processing_class=tokenizer, train_dataset=dataset, args=DPOConfig(**common, max_length=1024))
    elif config["method"] == "grpo":
        # TRL 1.x removed max_prompt_length; keep the existing left-truncation limit.
        def truncate_prompt(row):
            tokens = tokenizer.encode(row["prompt"], add_special_tokens=False)
            if len(tokens) > 1024:
                return {"prompt": tokenizer.decode(tokens[-1024:], skip_special_tokens=False)}
            return {"prompt": row["prompt"]}
        dataset = dataset.map(truncate_prompt)
        # This fixed reward is intentionally data-only: no user supplied Python, shell, or network access.
        def patch_reward(completions, reference_patch, **_kwargs):
            scores = []
            for completion, reference in zip(completions, reference_patch):
                output = completion if isinstance(completion, str) else str(completion)
                expected = reference if isinstance(reference, str) else str(reference)
                output_tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", output))
                expected_tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", expected))
                similarity = len(output_tokens & expected_tokens) / max(1, len(output_tokens | expected_tokens))
                is_unified_diff = "--- " in output and "+++ " in output and "@@" in output
                scores.append(round(min(1.0, 0.2 + similarity * 0.8) if is_unified_diff else 0.0, 6))
            return scores
        trainer = GRPOTrainer(model=model, processing_class=tokenizer, train_dataset=dataset,
                              reward_funcs=patch_reward,
                              args=GRPOConfig(**common, max_completion_length=1024, num_generations=2,
                                              generation_batch_size=2 * int(os.getenv("WORLD_SIZE", "1"))))
    else:
        raise ValueError("UNSUPPORTED_TRAINING_METHOD")
    result = trainer.train()
    # Training Operator sets RANK for multi-node DDP; only the global leader
    # may touch the shared output directory.
    if int(os.getenv("RANK", "0")) == 0:
        trainer.save_model("/output/model")
        tokenizer.save_pretrained("/output/model")
        Path("/output/metrics.json").write_text(json.dumps(result.metrics, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
