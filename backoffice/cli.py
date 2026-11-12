"""Command line.

  python -m backoffice.cli close --companies data/companies/C04 data/companies/C05 --llm hf --model <path> --var var
  python -m backoffice.cli close --companies data/companies/* --llm cache --model Qwen/Qwen2.5-7B-Instruct --var var   # offline re-run
  python -m backoffice.cli close --companies ... --llm openai --model gpt-4.1-mini            # any OpenAI-compatible endpoint
  python -m backoffice.cli verify-audit --var var
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .llm import Cached, HFLocal, OpenAICompatible, Scripted
from .pipeline import run_close


def model_key(model: str) -> str:
    """Stable model name for cache keys, the same for a local snapshot path or a hub id:
    .../models--Qwen--Qwen2.5-7B-Instruct/snapshots/<rev> and Qwen/Qwen2.5-7B-Instruct both give models--Qwen--Qwen2.5-7B-Instruct."""
    if "snapshots" in model:
        return Path(model).parts[Path(model).parts.index("snapshots") - 1]
    return "models--" + model.replace("/", "--")


def make_backend(kind: str, model: str | None):
    if kind == "hf":
        return HFLocal(model)
    if kind == "openai":
        return OpenAICompatible(model, base_url=os.environ.get("BACKOFFICE_LLM_BASE_URL"), api_key=os.environ.get("BACKOFFICE_LLM_API_KEY"))
    if kind == "fake":
        return Scripted(lambda s, u: '{"category": "other", "account_code": "?", "confidence": "low"}' if "JSON" in s else "No commentary.")
    if kind == "cache":
        return None
    raise ValueError(kind)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="backoffice")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("close", help="run the month-end close for one or more companies")
    c.add_argument("--companies", nargs="+", required=True)
    c.add_argument("--llm", default="cache", choices=["hf", "openai", "cache", "fake"])
    c.add_argument("--model")
    c.add_argument("--var", default="var")
    c.add_argument("--cache-dir", default="var/llm_cache")
    c.add_argument("--llm-for-all", action="store_true", help="also ask the model about transactions that memory resolves (evaluation)")
    c.add_argument("--no-redact", action="store_true")
    v = sub.add_parser("verify-audit")
    v.add_argument("--var", default="var")
    a = ap.parse_args(argv)

    if a.cmd == "close":
        backend = make_backend(a.llm, a.model)
        name = model_key(a.model) if a.model else getattr(backend, "name", None) or "cache"
        for cd in a.companies:
            cid = Path(cd).name
            llm = Cached(backend, Path(a.cache_dir) / f"{cid}.jsonl", offline=a.llm == "cache")
            llm.name = name
            run = run_close(Path(cd), llm, Path(a.var), llm_for_all=a.llm_for_all, redact=not a.no_redact)
            print(json.dumps({"company": cid, "seconds": run["seconds"], "status": run["status_counts"], "cache_hits": llm.hits,
                              "model_calls": llm.misses}), flush=True)
    elif a.cmd == "verify-audit":
        from .control import TenantStore
        for db in sorted(Path(a.var).glob("*/tenant.sqlite")):
            ok, bad = TenantStore(db, db.parent.name).verify_audit()
            print(db.parent.name, "audit chain intact" if ok else f"TAMPERED at entry {bad}")


if __name__ == "__main__":
    main()
