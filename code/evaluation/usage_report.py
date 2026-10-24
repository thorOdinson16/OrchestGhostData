"""Generate evaluation/usage_report.md from the model-usage ledger of the final full-dataset run.

    python code/evaluation/usage_report.py [ledger.jsonl]

Every model call made through ghost/llm.py appends one row (model, kind, cached, input/output tokens) to
code/cache/usage.jsonl; cache hits are recorded with zero tokens. Prices come from code/evaluation/pricing.json
(USD per 1M tokens); leave them null if the rate is unknown and the cost lines say so instead of guessing.
"""
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REQUESTS = 518          # rows of dataset/deletion_requests.csv in the final run
CLASSIFIED = 550        # requests classified in that run (518 evaluation + 32 public samples, resolved together)


def money(x):
    return "n/a (rate not supplied)" if x is None else f"${x:,.6f}"


def main():
    ledger = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "code" / "cache" / "usage_final_run.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    pricing = json.loads((HERE / "pricing.json").read_text(encoding="utf-8"))
    per_model = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0]))
    for r in rows:
        if r["cached"]:
            continue
        a = per_model[r["model"]][r["kind"]]
        a[0] += 1
        a[1] += r["input_tokens"]
        a[2] += r["output_tokens"]
    lines = ["# Token usage and cost - final full-dataset run", "",
             f"Run that produced `output.csv` ({REQUESTS} requests; `python code/main.py --rebuild-index` from an empty "
             "model cache, wall-clock about 2.6 minutes). Provider: OpenAI. The model is fixed to GPT-6 Luna "
             "(`gpt-6-luna`) for every call; no other model was used. Later deterministic-code changes were re-run from the "
             "on-disk cache, with one exception: scoping the text shown to the model to a single record (docs.py) changed 4 "
             "`docfact` prompts, which were called live and are included below. The 4 earlier calls they replaced are still "
             "counted, so the `docfact` row is an upper bound for a from-scratch run (by at most 4 calls).", ""]
    grand = [0, 0, 0]
    cost_total = 0.0
    cost_known = True
    for model, kinds in per_model.items():
        lines += [f"## {model}", "", "| Purpose | Calls | Input tokens | Output tokens | Total tokens |", "|---|---:|---:|---:|---:|"]
        m = [0, 0, 0]
        for kind, (c, i, o) in sorted(kinds.items()):
            lines.append(f"| {kind} | {c} | {i:,} | {o:,} | {i + o:,} |")
            m = [m[0] + c, m[1] + i, m[2] + o]
        lines.append(f"| **total** | **{m[0]}** | **{m[1]:,}** | **{m[2]:,}** | **{m[1] + m[2]:,}** |")
        price = pricing.get(model, {})
        pin, pout = price.get("input_per_1m_tokens_usd"), price.get("output_per_1m_tokens_usd")
        if pin is None or pout is None:
            cost, cost_known = None, False
        else:
            cost = m[1] / 1e6 * pin + m[2] / 1e6 * pout
            cost_total += cost
        lines += ["", f"Estimated cost for {model}: {money(cost)}"
                  + ("" if cost is None else f" (input ${pin}/1M, output ${pout}/1M tokens)"), ""]
        grand = [grand[0] + m[0], grand[1] + m[1], grand[2] + m[2]]
    tokens = grand[1] + grand[2]
    lines += ["## Overall", "",
              f"- Model calls: **{grand[0]}** (the OCR rows count only the first, uncached call per screenshot)",
              f"- Input tokens: **{grand[1]:,}**; output tokens: **{grand[2]:,}**; total: **{tokens:,}**",
              f"- Average tokens per request: **{tokens / REQUESTS:,.1f}** over the {REQUESTS} evaluation requests "
              f"({tokens / CLASSIFIED:,.1f} if spread over all {CLASSIFIED} requests that were classified)",
              f"- Estimated total cost: **{money(cost_total if cost_known else None)}**",
              f"- Estimated cost per request: **{money(cost_total / REQUESTS if cost_known else None)}**", "",
              "## Where the tokens go", "",
              "- `classify`: 550 request texts (518 evaluation + 32 public samples) in batches of 20, multilingual, returning "
              "intent, language and a short reason.",
              "- `ocr`: 11 application screenshots transcribed once each (high-detail vision input).",
              "- `docfact`: one short question per (document or screenshot, record) pair named by a data-catalog link or "
              "a PDF - does the record still exist, does it expose identifiers, is the flow/restore active.",
              "- Everything else (index building, identity resolution, linking, state reconstruction, policy, planning) is "
              "deterministic code and uses no tokens.", "",
              "Model responses are cached on disk by model, prompt and input, so any later re-run of the pipeline reuses "
              "them and costs nothing; this report counts only the uncached calls of the clean run above.", ""]
    (HERE / "usage_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("wrote", HERE / "usage_report.md")


if __name__ == "__main__":
    main()
