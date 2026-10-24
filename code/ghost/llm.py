"""Thin OpenAI wrapper: fixed model, on-disk response cache, usage ledger.

The API key is only ever read from the environment (loaded from .env); it is never printed or logged.
"""
import hashlib
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "code" / "cache"
USAGE_LOG = CACHE_DIR / "usage.jsonl"

# The user mandated GPT-6 Luna for the whole hackathon. Do not change without explicit permission.
MODEL = "gpt-6-luna"

_client = None


def client():
    global _client
    if _client is None:
        load_dotenv(ROOT / ".env")
        from openai import OpenAI

        _client = OpenAI()
    return _client


def _key(kind, payload):
    h = hashlib.sha256()
    h.update(MODEL.encode())
    h.update(kind.encode())
    h.update(payload.encode())
    return h.hexdigest()[:32]


def _record_usage(kind, usage, cached):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": MODEL,
        "kind": kind,
        "cached": cached,
        "input_tokens": 0 if cached else usage.prompt_tokens,
        "output_tokens": 0 if cached else usage.completion_tokens,
    }
    with open(USAGE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def chat_json(kind, messages, cache_material, retries=3):
    """Run a chat completion that must return a JSON object; cached by `cache_material`."""
    key = _key(kind, cache_material)
    path = CACHE_DIR / kind / f"{key}.json"
    if path.exists():
        _record_usage(kind, None, True)
        return json.loads(path.read_text(encoding="utf-8"))
    last = None
    for attempt in range(retries):
        try:
            r = client().chat.completions.create(
                model=MODEL, messages=messages, response_format={"type": "json_object"}
            )
            out = json.loads(r.choices[0].message.content)
            _record_usage(kind, r.usage, False)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            return out
        except Exception as e:  # network / JSON errors: retry
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM call failed for {kind}: {type(last).__name__}")
