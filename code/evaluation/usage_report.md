# Token usage and cost - final full-dataset run

Run that produced `output.csv` (518 requests; `python code/main.py --rebuild-index` from an empty model cache, wall-clock about 2.6 minutes). Provider: OpenAI. The model is fixed to GPT-6 Luna (`gpt-6-luna`) for every call; no other model was used. Later deterministic-code changes were re-run from the on-disk cache, with one exception: scoping the text shown to the model to a single record (docs.py) changed 4 `docfact` prompts, which were called live and are included below. The 4 earlier calls they replaced are still counted, so the `docfact` row is an upper bound for a from-scratch run (by at most 4 calls).

## gpt-6-luna

| Purpose | Calls | Input tokens | Output tokens | Total tokens |
|---|---:|---:|---:|---:|
| classify | 28 | 37,444 | 16,809 | 54,253 |
| docfact | 20 | 10,723 | 3,106 | 13,829 |
| ocr | 11 | 27,973 | 14,034 | 42,007 |
| **total** | **59** | **76,140** | **33,949** | **110,089** |

Estimated cost for gpt-6-luna: $0.024588 (input $0.1/1M, output $0.5/1M tokens)

## Overall

- Model calls: **59** (the OCR rows count only the first, uncached call per screenshot)
- Input tokens: **76,140**; output tokens: **33,949**; total: **110,089**
- Average tokens per request: **212.5** over the 518 evaluation requests (200.2 if spread over all 550 requests that were classified)
- Estimated total cost: **$0.024588**
- Estimated cost per request: **$0.000047**

## Where the tokens go

- `classify`: 550 request texts (518 evaluation + 32 public samples) in batches of 20, multilingual, returning intent, language and a short reason.
- `ocr`: 11 application screenshots transcribed once each (high-detail vision input).
- `docfact`: one short question per (document or screenshot, record) pair named by a data-catalog link or a PDF - does the record still exist, does it expose identifiers, is the flow/restore active.
- Everything else (index building, identity resolution, linking, state reconstruction, policy, planning) is deterministic code and uses no tokens.

Model responses are cached on disk by model, prompt and input, so any later re-run of the pipeline reuses them and costs nothing; this report counts only the uncached calls of the clean run above.
