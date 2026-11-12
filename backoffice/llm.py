"""Language-model backends behind one batched interface, plus a per-tenant response cache.

* ``OpenAICompatible`` any OpenAI-compatible endpoint: a hosted API, Azure OpenAI, or a self-hosted
  open-weight model served by vLLM or Ollama.
* ``HFLocal``          an open-weight model run in-process (data never leaves the server).
* ``Scripted``         deterministic replies for tests.
* ``Cached``           wraps any backend; one JSONL cache file per tenant, so a re-run is
  reproducible offline and no tenant's prompts are stored alongside another's.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Sequence

Prompt = tuple[str, str]  # (system, user)


class OpenAICompatible:
    def __init__(self, model: str, base_url: str | None = None, api_key: str | None = None, workers: int = 8):
        from openai import OpenAI

        self.client = OpenAI(base_url=base_url, api_key=api_key or "not-needed", max_retries=3, timeout=90)
        self.model, self.workers = model, workers
        self.name = model

    def _one(self, p: Prompt, max_tokens: int) -> str:
        r = self.client.chat.completions.create(model=self.model, temperature=0, max_tokens=max_tokens,
                                                messages=[{"role": "system", "content": p[0]}, {"role": "user", "content": p[1]}])
        return (r.choices[0].message.content or "").strip()

    def complete_batch(self, prompts: Sequence[Prompt], max_tokens: int = 300) -> list[str]:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(self.workers) as ex:
            return list(ex.map(lambda p: self._one(p, max_tokens), prompts))


class HFLocal:
    def __init__(self, model: str, batch_size: int = 24, dtype: str = "bfloat16"):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(model, padding_side="left")
        self.model = AutoModelForCausalLM.from_pretrained(model, dtype=getattr(torch, dtype), device_map="cuda")
        self.model.eval()
        self.bs = batch_size
        self.name = Path(model).parts[-3] if "snapshots" in model else model

    def complete_batch(self, prompts: Sequence[Prompt], max_tokens: int = 300) -> list[str]:
        out: list[str] = [""] * len(prompts)
        # sort by length so each batch pads little
        order = sorted(range(len(prompts)), key=lambda i: len(prompts[i][0]) + len(prompts[i][1]))
        for s in range(0, len(order), self.bs):
            idx = order[s:s + self.bs]
            texts = [self.tok.apply_chat_template([{"role": "system", "content": prompts[i][0]}, {"role": "user", "content": prompts[i][1]}],
                                                  tokenize=False, add_generation_prompt=True) for i in idx]
            enc = self.tok(texts, return_tensors="pt", padding=True).to(self.model.device)
            with self.torch.no_grad():
                gen = self.model.generate(**enc, max_new_tokens=max_tokens, do_sample=False, temperature=None, top_p=None, top_k=None,
                                          pad_token_id=self.tok.eos_token_id)
            for j, i in enumerate(idx):
                out[i] = self.tok.decode(gen[j, enc["input_ids"].shape[1]:], skip_special_tokens=True).strip()
        return out


class Scripted:
    def __init__(self, fn: Callable[[str, str], str]):
        self.fn, self.calls, self.name = fn, [], "scripted"

    def complete_batch(self, prompts, max_tokens=300):
        self.calls.extend(prompts)
        return [self.fn(s, u) for s, u in prompts]


class Cached:
    """Response cache keyed by model, prompt and token limit. One file per tenant."""

    def __init__(self, backend, path: Path, offline: bool = False):
        self.backend, self.path, self.offline = backend, Path(path), offline
        self.name = getattr(backend, "name", "unknown") if backend else "cache"
        self.mem: dict[str, str] = {}
        self.hits = self.misses = 0
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                rec = json.loads(line)
                self.mem[rec["k"]] = rec["v"]

    def key(self, p: Prompt, max_tokens: int) -> str:
        return hashlib.sha256(json.dumps([self.name, p[0], p[1], max_tokens]).encode()).hexdigest()

    def complete_batch(self, prompts, max_tokens=300):
        keys = [self.key(p, max_tokens) for p in prompts]
        todo = [i for i, k in enumerate(keys) if k not in self.mem]
        self.hits += len(prompts) - len(todo)
        self.misses += len(todo)
        if todo:
            if self.offline or self.backend is None:
                raise RuntimeError(f"{len(todo)} prompts not in cache {self.path} and no model loaded")
            res = self.backend.complete_batch([prompts[i] for i in todo], max_tokens)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                for i, v in zip(todo, res):
                    self.mem[keys[i]] = v
                    f.write(json.dumps({"k": keys[i], "v": v}) + "\n")
        return [self.mem[k] for k in keys]


def parse_json(text: str) -> dict | None:
    """First JSON object in a reply; tolerant of code fences and trailing prose."""
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M)
    start = t.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = t.find("{", start + 1)
    return None
