# HackerRank Orchestrate - October 2026

## Ghost Data Still Exists

Build an AI agent that audits whether a user's personal data was actually deleted.

A deletion workflow may report **Complete** while data still exists in databases, logs, support attachments, exports, search indexes, archives, or backups. Sync and restore processes may also bring deleted data back.

For each request, the agent must first determine whether deletion was actually requested. It then returns `COMPLETE`, `INCOMPLETE`, or `NOT_APPLICABLE`, reports any remaining data or recreation risk, and produces the ordered remediation and verification steps required to finish an authorized deletion safely.

Read [`problem_statement.md`](./problem_statement.md) for the complete challenge contract.

---

## Our Solution (setup and run)

A deterministic audit pipeline in Python with one fixed model, **GPT-6 Luna (`gpt-6-luna`)**, used only where language or
vision is needed: reading screenshots, classifying multilingual requests, and interpreting PDFs/screenshots for a named
record. Every decision about what must be deleted, redacted, anonymized, retained, disabled or blocked is made by code
that reads `dataset/deletion_rules.csv`.

### Setup

```bash
python -m venv .venv            # keep the venv at the repo root, outside code/
.venv/Scripts/activate          # macOS/Linux: source .venv/bin/activate
pip install -r code/requirements.txt
```

Create an uncommitted `.env` in the repository root (the key is read from the environment only; it is never printed,
logged, or written anywhere):

```text
OPENAI_API_KEY=...
```

### Run

```bash
python code/main.py                      # all 518 requests -> ./output.csv (first run also builds the index + calls the model)
python code/main.py --dataset sample     # the 32 public samples -> code/cache/sample_pred.csv
python code/evaluation/evaluate.py       # score the sample predictions against dataset/sample_output.csv
python code/evaluation/validate_output.py   # structural check of ./output.csv against the submission contract
python code/evaluation/citation_check.py    # every cited step: manifest + capture time + does the file back the claim
python code/evaluation/main.py              # the whole evaluation; exit code 1 if any required stage fails
python code/evaluation/run_custom.py     # our 5 extra evaluation cases (synthetic, built on the fly)
python code/evaluation/slot_validation.py   # leave-one-group-out check of the activity-based account pairing
python code/debug_case.py case_123       # explain how one case was resolved
python code/package_zip.py               # build code.zip (no dataset, cache, venv, or secrets)
```

Model responses (OCR, classification, document interpretation) are cached under `code/cache/`, keyed by model, prompt
and input, so re-runs are deterministic and free; token usage of every call is appended to `code/cache/usage.jsonl`.

### How it works

1. **Reusable index** (`ghost/index.py`) - every evidence file (SQL via SQLite, JSON, 12 log dialects, text, PDF text
   layer, model-OCR of screenshots) is parsed once into a SQLite `facts` table (one row per observation of one
   resource: system, resource id, record type, observation time, identifiers, state attributes) plus a `coverage` table
   (which snapshot/collection a file covers, even when empty) and a `docs` table. All later stages query this index.
2. **Request classification** (`ghost/classify.py`) - GPT-6 Luna decides whether the text (any language) authorizes
   deletion; access, correction, export, marketing opt-out and "do not delete" requests become `NOT_APPLICABLE` / `[]`.
3. **Identity resolution** (`ghost/identity.py`) - the request email is matched exactly (case-insensitive) against current
   and historical emails; `user_audit_log.csv` supplies earlier emails/phones, and SHA-256 hashes of every historical
   email are derived so hash-only records link too. No fuzzy matching anywhere.
4. **Data mapping** (`ghost/mapping.py`, `ghost/docs.py`, `ghost/activity.py`, `ghost/slots.py`) - facts are attached to
   the subject by exact email / hash / user id / phone (also emails inside message text and log traces), plus
   data-catalog links for documents and screenshots. Records that carry no identifier (an account that returns 404, an
   anonymized analytics row) are tied to a case through the deletion-worker activity stream.
5. **State at `audit_at`** (`ghost/state.py`) - only observations timestamped inside the case window count; replicas are
   compared with primaries (any copy that still exposes data counts); evidence captured after `audit_at` is never cited;
   workflow statuses and activity results are treated as claims.
6. **Policy layer** (`ghost/policy.py`) - maps each resource's data category to DELETE / REDACT / ANONYMIZE / RETAIN /
   disable-flow / block-restore from `deletion_rules.csv`, decides if it is violated, and chooses finding, remediation
   and verification operations.
7. **Planner** (`ghost/planner.py`) - emits phase-ordered steps (findings, then remediation with data flows and restores
   first, then verification), sorted by operation, system and resource, numbered from 1.

Evaluation reports: `code/evaluation/evaluation_report.md` and `code/evaluation/usage_report.md`.

---

## Quick Start

Clone the repository and move into the project directory:

```bash
git clone https://github.com/interviewstreet/hackerrank-orchestrate-october26.git
cd hackerrank-orchestrate-october26
```

Start your AI coding tool from the **repository root** so it automatically reads [`AGENTS.md`](./AGENTS.md) and logs your session turns to `log.txt` in the root directory.

Build your solution in `code/main.py`, or use another language and document its setup and run command clearly in your README.

Your solution must:

- Read the input files from `dataset/`
- Generate one prediction row for every request in `dataset/deletion_requests.csv`
- Write the final predictions to `output.csv` in the repository root

Run the starter Python entry point with:

```bash
python3 code/main.py
```

The blank template at `dataset/output.csv` is provided as a reference. Your final generated file for submission must be the root-level `output.csv`.


## Dataset

All participant-facing files are in `dataset/`:

| File | Contents |
| --- | --- |
| `deletion_requests.csv` | 518 requests |
| `sample_deletion_requests.csv` | 32 sample requests |
| `sample_output.csv` | Correct sample answers |
| `user_identities.csv` | Current user identities |
| `user_audit_log.csv` | Historical identity changes |
| `system_activity.csv` | Activity across services and data processes |
| `deletion_rules.csv` | Required treatment for each data category |
| `evidence_files.csv` | Manifest of supplied evidence files |
| `output.csv` | Blank output template |

Evidence is stored under `dataset/evidence/` and may include PDFs, images, logs, JSON, plain text, or SQL snapshot exports. SQL captures may contain a primary database, regional replicas, a reporting replica, or a failover replica so replication drift can be investigated directly. The `.sql` files are readable as text and executable with SQLite.

Only `deletion_requests.csv` requires answers. The other files provide evidence and context.

---

## Output

Create `output.csv` with these columns in this exact order:

```text
case_id,deletion_status,steps_json
```

Produce exactly one row per request. `deletion_status` is `COMPLETE`, `INCOMPLETE`, or `NOT_APPLICABLE`. `steps_json` is an ordered JSON array containing findings, remediation actions, and verification checks. Use an empty array (`[]`) for `NOT_APPLICABLE`. Evidence references may be a `file_id`, `activity_id`, or `audit_id`. Determine the resource state at `audit_at` from the available evidence; reported workflow statuses and operation results are claims. Use `dataset/sample_output.csv` as the format reference.

The final file must contain 518 data rows sorted by `case_id` (plus the header row). The grader parses `steps_json` as JSON, so object-key order and whitespace do not matter; array order does. Order findings, remediation, and verification in that phase order. Within a phase, follow the operation order in the problem statement, then sort by `system_id` and `resource_id`.

---

## Evaluation

`output.csv` will be compared with hidden canonical answers. Scoring considers:

- deletion assessment accuracy;
- current and historical identity resolution;
- discovery of remaining data and recreation risks;
- correct policy application and evidence references; and
- completeness and ordering of remediation and verification steps.

Your `code.zip` must include:

```text
code/evaluation/evaluation_report.md
code/evaluation/usage_report.md
```

- `evaluation_report.md` must cover case, row, field, and sequence accuracy, at least three additional evaluation cases created by you, failures found, and improvements made.
- `usage_report.md` must cover the final full-dataset run, including model providers and names, model calls, input and output tokens, total and average tokens per request, and estimated total and per-request cost. Include per-model and overall totals when using multiple models.

**Important:** Place `evaluation_report.md` and `usage_report.md` inside `code/evaluation/` (matching `code/evaluation/main.py` in this starter layout), write your complete evaluation metrics, custom evaluation test cases, and token/cost breakdowns directly inside those Markdown files, and keep prompts in source code or Markdown files. Automated code evaluation ignores raw data files (`.csv`, `.tsv`, `.json`, `.jsonl`, `.sql`, `.log`), so content stored only in those formats will not be visible to the code judge or AI interviewer.

---

## Chat Transcript

This repository includes [`AGENTS.md`](./AGENTS.md), which instructs compatible AI coding tools to append conversation summaries to `log.txt` in the repository root:

| Platform | Path |
| --- | --- |
| macOS / Linux | `<repo root>/log.txt` |
| Windows | `<repo root>\log.txt` |

- Always launch your AI coding agent from the repository root (or explicitly ask it to read `./AGENTS.md` first) so every turn is logged to `log.txt` with the required `User Prompt`, `Agent Response Summary`, `Actions`, and `Context` sections.
- Upload `log.txt` directly as your `chat_transcript` submission on HackerRank.
- Do not include API keys, tokens, credentials, or secrets in prompts, code, configuration, or `log.txt`.

---

## Packaging `code.zip` and Submission

| Item | Description |
| --- | --- |
| `code.zip` | Runnable solution, prompts, configuration, `README.md`, and `evaluation/` reports |
| `output.csv` | One answer row for each of the 518 requests in `dataset/deletion_requests.csv` |
| `chat_transcript` | The root `log.txt` file generated by your coding agent |
| AI interview | Live explanation of your architecture, approach, and evaluation |

You may use any programming language, model, framework, or retrieval approach. Include clear setup and execution instructions in `README.md` inside `code.zip`.

To package `code.zip` from the repository root without bundling the dataset:

**macOS / Linux:**

```bash
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store"
```

**Windows (PowerShell):**

```powershell
Compress-Archive -Path README.md, code -DestinationPath code.zip -Force
```

When packaging `code.zip`:

- Keep your evaluation reports in `code/evaluation/` so they are bundled automatically with `code` (or add `evaluation` to the archive command above if you placed them in a top-level `evaluation/` directory).
- Do **not** include `dataset/`, virtual environments (`.venv`, `venv`, `node_modules`), or build caches (`__pycache__`). On Windows, keep `.venv` at the repository root (outside `code/`) and remove any `code/__pycache__` folders before running `Compress-Archive`.
- Do **not** include nested archives (`.zip`, `.tar`, `.gz`, `.tgz`, `.7z`, `.rar`) inside `code.zip` — submissions containing nested archive files will be rejected.

Before submitting, confirm that:

- `output.csv` has the exact header `case_id,deletion_status,steps_json` and contains all 518 data rows sorted by `case_id`;
- every `steps_json` value is a valid JSON array with consecutive step numbers starting at `1`;
- findings cite valid evidence IDs, and `COMPLETE` verification steps cite evidence proving the current state;
- proposed remediation and future verification steps use an empty `evidence_id` (`""`);
- `code.zip` includes your runnable code, `README.md`, `code/evaluation/evaluation_report.md`, and `code/evaluation/usage_report.md`;
- `log.txt` follows the `AGENTS.md` turn format and is uploaded to the `chat_transcript` slot; and
- no API keys, credentials, real personal data, or sensitive configuration values are included.
