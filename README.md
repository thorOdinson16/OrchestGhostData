# Ghost Data Still Exists - deletion-audit agent

A deletion workflow can report **Complete** while a person's data is still in databases, replicas, logs, support
attachments, exports, search indexes, analytics tables, archives or backups, and while sync or restore processes can
recreate it. This project audits such requests. For each request it decides whether deletion was actually requested,
reconstructs what exists at the audit time from the supplied evidence, applies the retention/deletion policy, and
returns `COMPLETE`, `INCOMPLETE` or `NOT_APPLICABLE` with ordered findings, remediation and verification steps.

Design principle: a deterministic Python pipeline makes every decision; one fixed model (**GPT-6 Luna**, `gpt-6-luna`)
is used only to read language or images (request classification, screenshot OCR, interpreting one record in a document).
Reported workflow statuses and activity results are treated as claims, never as proof.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate          # macOS/Linux: source .venv/bin/activate
pip install -r code/requirements.txt
```

Put the API key in an uncommitted `.env` at the repository root (it is read from the environment only and is never
printed or logged):

```text
OPENAI_API_KEY=...
```

## Run

```bash
python code/main.py                        # all requests in dataset/deletion_requests.csv -> ./output.csv
python code/main.py --dataset sample       # the 32 labelled sample requests -> code/cache/sample_pred.csv
python code/main.py --rebuild-index        # force a rebuild of the evidence index
python code/debug_case.py case_123         # explain how one case was resolved
python -m unittest discover -s code/tests  # unit and regression tests
python code/evaluation/main.py             # the whole evaluation; exit code 1 if any required stage fails
```

Model responses are cached under `code/cache/` (keyed by model, prompt and input), so re-runs are deterministic and
free; every call's token usage is appended to `code/cache/usage.jsonl`. The first run builds the evidence index and
calls the model.

## Input and output

Inputs are read from `dataset/`: the requests (`email`, `requested_at`, `reported_status`, `reported_at`, `audit_at`,
`request_text`), current identities and their change log, a global activity stream, `deletion_rules.csv`, and a manifest
of evidence files (PDFs, screenshots, logs, JSON, text, SQLite-compatible SQL exports).

Output is `output.csv` with columns `case_id,deletion_status,steps_json`, one row per request, sorted by `case_id`.
`steps_json` is an ordered array of steps `{step_no, phase, system_id, resource_id, operation, state, evidence_id}` in
the phases FINDING, REMEDIATION, VERIFICATION. Findings and `COMPLETE` verifications cite the evidence file that proves
them; remediation and future verification steps have an empty `evidence_id`. `NOT_APPLICABLE` rows are `[]`.

## How it works

1. **Evidence index** (`ghost/index.py`, `logparse.py`, `ocr.py`) - every evidence file is parsed once into a SQLite
   `facts` table (one row per observation of one resource: system, resource id, type, observation time, identifiers,
   state attributes), a `coverage` table (which snapshot or collection a file covers, even when empty) and a `docs`
   table. The index stores a fingerprint of the schema, parser code, OCR model, manifest and every evidence file, and is
   rebuilt when any of them changes.
2. **Request classification** (`ghost/classify.py`) - the model decides whether the text, in any language, authorizes
   deletion. Each case must receive exactly one of `DELETE`/`NOT_DELETE`; missing, malformed or duplicate answers are
   retried and then raise `ClassificationError` - a failure is never turned into `NOT_APPLICABLE`.
3. **Identity** (`ghost/identity.py`) - exact, case-insensitive matching of the request email against current and
   historical emails, resolved **as of `audit_at`** (identifiers introduced after the cutoff are not used; inconsistent
   history is marked ambiguous). SHA-256 hashes of all emails are derived so hash-only records link too. No fuzzy matching.
4. **Linking** (`ghost/mapping.py`, `docs.py`, `activity.py`, `slots.py`) - facts attach to the subject by exact email,
   hash, user id or phone, by emails in message text, and by catalog links for documents and screenshots. PDFs are
   split into records so identifiers attach record by record; attribution (whose record) is kept apart from exposure
   (what the record shows). Records that carry no identifier (an account returning 404, an anonymized analytics row) are
   paired with a case through the deletion-worker activity stream using slot structure, result class and seconds-phase
   agreement; a pairing is made only when the evidence separates it from the alternatives, otherwise the identity is
   recorded as unresolved and nothing is asserted about it.
5. **State at `audit_at`** (`ghost/state.py`, `pipeline.py`) - observation time decides discovery; only files captured at
   or before `audit_at` may determine state or be cited. Presence is **three-valued** (PRESENT / ABSENT / UNKNOWN):
   missing evidence is unknown, never absence. Absence needs an observation of absence or a citable snapshot that speaks
   for the subject (holds their records or declares system-wide scope). Replicas are compared with primaries.
6. **Policy** (`ghost/policy.py`) - each resource's category is mapped to DELETE / REDACT / ANONYMIZE / RETAIN /
   disable-flow / block-restore from `deletion_rules.csv`. Rule conditions are evaluated TRUE / FALSE / UNKNOWN from
   citable evidence; a rule whose condition is FALSE emits nothing; conflicting or unresolved rules stay unresolved.
7. **Verdict and planner** (`ghost/pipeline.py`, `planner.py`) - a case is `COMPLETE` only if nothing is violated and no
   mandatory check is open (unknown state, unresolved rule, unresolved subject). Open checks appear as future
   verification steps, never as findings. Steps are ordered by phase, operation, system and resource and numbered from 1.

## Evaluation and tests

- 199 unit and regression tests (`code/tests`), including adversarial cross-component cases and tests of the evaluation
  runner itself.
- `python code/evaluation/main.py` runs 10 required stages (tests, sample scoring, output contract, custom and
  adversarial cases, pairing validation, citation checks, identity metrics) and fails if any required stage fails, is
  skipped, or produces nothing.
- Measured on the 32 labelled samples: status accuracy 32/32, step precision 1.0000, recall 0.9482, F1 0.9734.
- Citation checks over all answers: 1,198 cited steps, all captured at or before `audit_at`, all citing a file that
  supports the claim about that resource.
- Reports with full metrics, custom cases, failures found and fixes: `code/evaluation/evaluation_report.md`; token
  and cost summary of the final run: `code/evaluation/usage_report.md`.

## Known limitations and assumptions

- **No ground truth** exists for the full request set. Identity pairings made through activity (not identifiers) and the
  unresolved ones are counted, not verified; a foreign event that mimics a true event's slot, class and phase cannot be
  distinguished.
- **Unattributed resources.** A `COMPLETE` verdict can rest on an account, analytics or legal-hold row that could not be
  attributed to the person (72 cases; 68 outside the labelled samples). Blocking `COMPLETE` on these
  (`pipeline.UNRESOLVED_RESOURCE_BLOCKS_COMPLETE`) is off because it contradicts four labelled samples (status 28/32).
  They are listed in `Pipeline.complete_with_open_identity`.
- **Retention.** A record without a usable retention date is treated as retained while it still exists
  (`policy.RETAIN_BY_EXISTENCE`). The specification does not say this; it matches all 16 labelled audit records.
- Complete resource discovery cannot be proven from the data. Citation checks show the cited file is consistent with the
  claim, not that the claim is true.
- A backup is assumed restorable unless the evidence blocks it; the model's "record absent" answer for documents is
  stored but not used.
- Only two PDF layouts are segmented (`Record` cards and pipe-delimited rows); screenshot association rests on catalog links.
