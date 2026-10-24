# Evaluation report - Ghost Data Still Exists

Solution: deterministic audit pipeline with a fixed model (GPT-6 Luna, `gpt-6-luna`) for OCR, multilingual request
classification and document interpretation; deletion decisions come from a policy layer driven by
`deletion_rules.csv`. See `README.md` for architecture and commands.

There are no labels for the 518 evaluation requests, so quality is measured on (1) the 32 public samples with their
official answers, (2) five extra cases we designed ourselves, and (3) a leave-one-group-out check of the one statistical
step in the pipeline (activity-based account pairing). Everything below is reproducible with the commands shown.

## 1. Public samples (32 cases, 193 reference steps)

`python code/main.py --dataset sample && python code/evaluation/evaluate.py`

| Metric | Value |
|---|---:|
| Case-level deletion-status accuracy | 32/32 = **1.0000** |
| Exact row accuracy (status and every step identical) | 29/32 = 0.9062 |
| Step precision (phase, system, resource, operation, state) | **1.0000** |
| Step recall | 0.9793 (189/193) |
| Step F1 | 0.9895 |
| Field-level accuracy on matched steps (6 fields incl. `evidence_id`) | **1.0000** |
| Sequence accuracy (LCS of the step lists / longer list) | 0.9793 |

Every step the agent emits is correct, including its cited evidence id. The four missed steps:

| Case | Missed step | Why it cannot be derived |
|---|---|---|
| case_18 | `VERIFY_RECORD_PRESENT audit_record_6645` | The id appears nowhere in the participant data. |
| case_20 | `VERIFY_RECORD_PRESENT audit_record_5569` | Same. |
| case_04 | `VERIFY_RECORD_ABSENT account_004` | Hand-crafted case: no evidence or activity names the account. |
| case_04 | `VERIFY_RECORD_ANONYMIZED analytics_901` | The anonymized analytics row has no identifier and no linking account event. |

The same limit applies to the evaluation set: for about a hundred requests no security-audit record id for the user exists in
any file, so no audit-retention step can be produced for them (we never invent ids).

## 2. Additional evaluation cases (synthetic, fictional people)

`python code/evaluation/run_custom.py` builds a small dataset under `code/evaluation/custom/` (SQL snapshot, handover text,
two JSON ledgers, identities, audit log, activity, requests) and a hand-derived `expected_output.csv`, then runs the same
pipeline end to end including the model for classification.

| Case | Scenario | What it checks | Result |
|---|---|---|---|
| case_c1 | German deletion request that arrives under the user's **old** email; SQL snapshot with a search entry keyed by the old email, an analytics row identified **only by the SHA-256 of the old email**, an active connector, a restorable backup, an already redacted support record, a retained audit record, and an account known only from deletion-worker activity | historical identity, hash-only linking, activity-based account link, all policy actions (delete / anonymize / disable / block / retain / verify-redacted), 15 ordered steps | exact match |
| case_c2 | Spanish request to update a phone number and **not** to delete the account | multilingual non-deletion -> `NOT_APPLICABLE` with `[]` | exact match |
| case_c3 | Japanese deletion request; handover record shows account gone, support record redacted, flow disabled, backup blocked, audit record present, plus a different customer with a near-identical email | `COMPLETE` path: verification-only steps citing the evidence file; exact matching rejects the look-alike | exact match |
| case_c4 | French deletion request; one ledger captured **before** `audit_at` shows account and search entry present, a second ledger captured **after** `audit_at` shows them deleted; a look-alike email (`fadell`) also present | later evidence must not change the answer or be cited; no fuzzy matching | exact match |
| case_c5 | English request from `greta.langer@...`, a near-identical neighbour of case_c1's user | exact matching in both directions: only her own search entry is found | exact match |

Aggregate on the five: case-status accuracy 1.0, exact-row accuracy 1.0, step F1 1.0, sequence accuracy 1.0.

## 3. Validation of the activity-based account pairing

Many accounts are named only by a deletion-worker event (the evidence just says "404"). Requests that share a
`reported_at` form a pool whose account events are matched to cases (`ghost/slots.py`). To check it without labels:
for 57 pools (157 cases) whose accounts are identified by exact identifiers, we hid the answer and re-derived it from
activity alone (`python code/evaluation/slot_validation.py`):

A pairing is a claim about identity, so it is made only when independent evidence supports it (`ghost/slots.py`):

1. **Slot structure.** The k cases sharing a `reported_at` have their account events one each in the k equal bands of
   [-75, -44] minutes before it (all 86 identifier-confirmed pools fit; the widest overshoot was 1.1 min, tolerance 2). A pool
   whose events do not fit, or a subset containing an event outside its band, is not paired (2 evaluation pools fail this).
2. **Margin.** Every candidate assignment within `MARGIN` = 6 points of the best (class 10, each agreeing seconds-phase 3)
   is kept; a case whose event differs among them stays unresolved.
3. **Contradiction.** If any of the case's other events can be phase-checked, ALL must agree with the chosen account
   (215 of 218 identifier-confirmed pairs agree on every checkable event).
4. **Corroboration (new).** The result class is a workflow claim shared by many events and the bands only show that an
   event belongs to the pool, so a pairing also needs the case's OWN independent evidence: at least one of its other
   activity events agreeing with the account's seconds-phase. Class + band alone no longer identifies anyone.

The wide-window path for hand-crafted cases (a single unclaimed account event within 30 minutes of `reported_at`) is kept
because the samples need it; it rests on base rate, not on corroboration (about 8% of such hours contain an unrelated
account event), is tagged `wide_window` and accounts for 11 of the 204 activity-paired accounts.

`python code/evaluation/pairing_robustness.py` on the 57 identifier-confirmed groups (157 cases, answers hidden, phase
statistics learned without the held-out audit batch):

| Check | Correct | Wrong | Unresolved |
|---|---:|---:|---:|
| Real pools | 137 | 0 | 20 |
| Rotation-slot hypothesis off | 123 | 0 | 34 |
| MARGIN 3 / 6 / 9 / 12 | 137 / 137 / 137 / 125 | 0 | 20 / 20 / 20 / 32 |
| D1 one foreign extra event in the pool | 123 | 0 | 34 |
| D2 a case's true event replaced by a foreign one (orphaned case) | other cases 27 | 0 (orphan 0 of 57 wrong) | 73 other + 57 orphan |
| D3 worst case: replacement in the same band, same class, random phase (20 seeds x 57) | other cases 1683 | orphan 88 of 1140 (7.7%), other 3 | 1052 orphan + 314 other |
| D4 replacement also keeps the true phase (indistinguishable) | other cases 1702 | orphan 979 of 1140 (86%) | 161 orphan + 298 other |
| Pools without a confirmed member: class-only vs phase-only pairing | 206 agree, 0 disagree | | |

The missing-event problem is reduced, not solved. Foreign and misplaced decoys are now left unresolved (D1, D2: 0 wrong;
before this change 7 of 57 orphans, 12%, were paired wrongly). A decoy that matches the true event's slot, result class and
phase (D4) carries exactly the evidence a true event would and cannot be told apart; a decoy that matches slot and class
but not phase still slips through 7.7% of the time by chance phase agreement (D3). The price is coverage: 137 of 157
confirmed cases (87%) are paired instead of 157, and in the evaluation set unresolved accounts rise from 33 to 88.
No pool in the evaluation set has fewer account events than cases except the hand-crafted ones, so a missing true event
is not known to occur there; the safeguards are defensive.

## 3b. Independent consistency check (not used for decisions)

The deletion worker's activity stream is a claim and is never used to decide a verdict, but its *shape* is an
independent signal: operations that finished cleanly are logged shortly before `reported_at`, the ones that went wrong
after it. `python code/evaluation/consistency_check.py` compares that shape with our evidence-based verdicts:

| Level | Agreement |
|---|---:|
| Resource level (non-account resources with activity: 912) | 909/912 = **0.997** |
| Case level (540 deletion cases: INCOMPLETE iff some linked resource shows the "went wrong" shape) | 527/540 = 0.976 |

The 3 resource-level exceptions are the hand-crafted early samples (case_06, case_08, case_10) whose timing was written
by hand; the 13 case-level exceptions are those plus the screenshot/PDF cases (33-63) that have no activity at all.

## 4. Output of the final run on the 518 evaluation requests

| | |
|---|---:|
| Rows | 518 (sorted, one per `case_id`), 0 contract violations (`python code/evaluation/validate_output.py`) |
| `COMPLETE` / `INCOMPLETE` / `NOT_APPLICABLE` | 153 / 355 / 10 |
| Steps emitted | 3,253 |
| Checks enforced | step numbers consecutive from 1; phase order; operation, system, resource order; state matches operation; remediation and future verification carry no evidence id; every cited file captured at or before `audit_at`; `COMPLETE` rows contain verification steps only |

Ten non-deletion requests (profile corrections, marketing opt-out, data copy, invoices, login help, etc.) were recognised
in English by the model; the sample and custom cases cover other languages.

## 5. Failures found and improvements made

Each item was found by diffing predictions against the samples or by inspecting resolved cases; "effect" is the observed
change.

1. **Closing wording read as non-deletion.** "Close the account completely and make sure it can no longer send my data"
   (sample case_15) was classified `NOT_DELETE`. Prompt now states that closing together with removing/stopping data use is
   deletion. Effect: status accuracy 30/32 -> 32/32 with later fixes.
2. **Evidence from the user's other requests leaked in.** The same person appears in several requests months apart; an
   earlier request's attachment and an earlier account lookup were attached to the later case. Fix: only observations
   timestamped inside `[requested_at, audit_at]` count, and resources whose deletion-worker activity falls in another
   request's window are dropped. Effect: removed the extra `object_19` / `tombstone_17` / `account_6523` steps.
3. **Observation time vs citation time.** Some records are visible only in files captured after `audit_at` whose entries are
   timestamped before it (they name the retained audit record). Fix: observation time decides whether a fact is known at
   `audit_at`; capture time decides whether a file may be cited (never after `audit_at`). SQL rows now carry the snapshot
   time instead of `updated_at`. Effect: step recall 0.948 -> 0.974.
4. **Secondary-identifier records.** A support or analytics row that is only a contact/alias lookup (keyed by the old email
   or its hash) must be deleted, not redacted (sample case_10, case_12). Detected from the record kind, not from the link.
5. **Silent parser loss (found by inspection, no label).** The pipe-delimited log dialect dropped all resource lines of two
   files; ledger-style `operational_record_history` snapshots, `uploaded_by` attachments, `download_available` exports and
   emails embedded in support message text were also unread. Fixing these changed 15 + 28 + 23 evaluation rows (mostly
   adding findings, 8 rows flipping `COMPLETE` -> `INCOMPLETE`).
6. **Later observation must override earlier ones.** A record present at 09:41 and gone at 11:00 in the same log used to be
   OR-ed into "present". Flags are now merged by time (copies observed at the same instant, e.g. primary + replicas, are
   still OR-ed because any exposed copy counts). Effect: 2 rows flipped to `COMPLETE`.
7. **Ambiguous accounts and anonymized rows.** First version picked any unclaimed account event in a wide window, which swapped
   accounts between cases that share a `reported_at` (sample case_23, case_28/29). Replaced by pool-wise pairing with the
   reported-result class, the rotation structure of the slots and seconds-phase agreement (section 3). A first analytics
   matching window that was too loose (T-40 min) assigned anonymized rows to the wrong case; narrowing to the generator's
   stage window and exact phase moved 14 rows. Effect on samples: precision 0.995 -> 1.000.
8. **Screenshot heuristics.** A keyword test on the screenshot text treated an export that the console shows as "Ready" as
   gone (the page also has an "Expired" tab, which a word match cannot tell from a status). Replaced by a per-record model question ("does the evidence show record X still exists / exposes identifiers / is active") with the model's quote
   kept in the fact for audit.
9. **A newer snapshot that omits a record means the record is gone.** Case_126 was marked `INCOMPLETE` from a stale
   observation (account present on 08-11) although a newer citable SQL snapshot of the same batch no longer listed it. The
   rule is "a newer, citable, full-table snapshot that contains other records of the same subject supersedes older
   observations"; requiring the same subject avoids using another batch's empty tables (a first version of the rule
   wrongly cleared sample cases 05 and 24 and was caught by the sample scoring). Effect: 1 evaluation row flipped to
   `COMPLETE`, matching the activity-shape check above.

10. **Temporal leakage in state reconstruction.** `state_facts` fell back to observations from files captured after
    `audit_at` whenever no citable file mentioned a resource, and a missing observation was read as "absent". Now only
    citable files determine state and citations; later-captured files are used solely for discovery; with no citable
    evidence the state is *unresolved* (never absent) unless a citable full snapshot omits the record; `COMPLETE` rows
    list only what citable evidence proves. Regression tests: `python -m unittest discover -s code/tests -v` (6 tests:
    late evidence, mixed evidence, unknown state, absence proof, superseding snapshots, discovery). Effect: 4 evaluation
    rows changed citation only (case_543/545/547/549 now cite the batch snapshot `evidence_52`); no verdict changed; 6
    accounts in `INCOMPLETE` rows (case_282, 526-529, 531) are now unresolved rather than assumed absent.

11. **Policy conditions were ignored.** `rule_for()` took the first rule of a category and several categories were
    hard-coded. Rules now apply only when their condition evaluates TRUE, three-valued (TRUE/FALSE/UNKNOWN) from
    evidence-derived context (retention date, hold end date, owners of an attachment, attributable identifiers, restore
    flag, account status, conversation content); conflicting TRUE rules, a missing rule or a condition without an evaluator
    are *unresolved* (no finding, no remediation, a verification only in `INCOMPLETE` rows, nothing in `COMPLETE` rows) instead
    of a silent DELETE. FALSE on "still attributable" / "contains an email" keeps the verification of the already-met state;
    FALSE elsewhere means the rule does not apply. `code/tests/test_policy.py` covers every conditional rule for TRUE, FALSE
    and UNKNOWN plus conflicts and missing rules. Effect on the data: no row, step or verdict changed. The run now has
    121 audit-record and 4 legal-hold conditions UNKNOWN. Review of those: 121 audit records are observed (timestamped
    before `audit_at`) only in later-captured files and none of their observations carries a retention date; the legal holds are
    activity-linked or observed only in a later-captured file. Retention/hold dates and every other condition input are taken
    from citable evidence only (a first version let one legal-hold date from a later-captured file, case_432, through; fixed,
    no output changed). UNKNOWN is therefore not applied automatically: 213 audit records resolve TRUE from a citable
    observation and 104 from a citable retention date. Support messages: 158 TRUE from content/ticket fields, 1 from a
    screenshot whose own transcribed text has a message table / order reference (case_53; the model's quote alone no longer
    counts), 19 from a source record-type label only (handover/ledger lines), which is a documented interpretation. FALSE
    means "rule does not apply, emit nothing" (except the attributable/contains-email conditions, which keep their
    verification); this is a deliberate decision recorded in the `policy.py` docstring and tested.

13. **Orphaned cases (true event missing).** Corroboration, slot-band structure and all-events-agree contradiction rules
    (section 3) replace "highest score wins". Evaluation set: accounts named by exact identifiers 248, paired through activity
    204 (193 with phase corroboration, 11 via the wide-window path), unresolved 88; anonymized analytics by identifiers 79, by
    activity 69, unresolved 6; legal holds 8 by identifiers, 1 by activity. Against the previous version 56 rows changed, no
    verdict: 54 account-absent, 40 analytics-anonymized and 1 legal-hold steps were dropped as no longer supported, 5 account
    and 1 analytics steps were added (cases the new band filter made unambiguous). Samples: status 32/32, step precision 1.0000,
    recall 0.9482 (was 0.9793; six correct but uncorroborated account/analytics steps in case_21, 25, 31, 32 are now left
    out), F1 0.9734. Custom cases: status and precision 1.0, recall 0.9655 (the account known only from a class match, case_c1,
    is no longer asserted); the hand-derived expected output was not changed.
12. **Explicit uncertainty in identity matching.** Presence is now PRESENT / ABSENT / UNKNOWN (`ResourceState.presence`).
    Pairing of accounts and of identifier-less analytics/legal-hold rows no longer picks a best guess: ties and near-ties
    (< 6 points), a lone candidate whose result class contradicts the case (without phase support), oversized pools and
    records claimed by two cases all stay unresolved and produce no account/analytics/legal step, state or citation
    (`Pipeline.unresolved_identities` records the reason and candidates). Evaluation set: accounts named by exact identifiers
    248, paired through activity 259, unresolved 33 (21 ambiguous, 12 with no account event in the pool window, mostly
    the screenshot/PDF cases); anonymized analytics rows by identifiers 79, by activity 110, unresolved 8 (the phase table is keyed by event type, target system and pre/post, because tombstone and support redactions share an event type but not a phase); legal holds 8 by
    identifiers, 2 by activity. Effect on outputs: 25 rows changed, none in verdict; 21 account-absent and 12
    analytics-anonymized steps were dropped because their identity could not be established; samples unchanged. Later-captured
    files may still discover a resource through an exact identifier but never set its state or citation (tests).

14. **Document identifiers attached to the wrong record.** `pdf_facts` gave every resource id in a PDF the first (sorted) email
    and user id found anywhere in the document, and `doc_facts` matched a catalog link's display reference as a plain
    substring anywhere in a document. Latent faults in the fixed dataset (its PDFs hold one resource each), with one real
    side effect: the display reference `user_17` of `tombstone_17` matched the register of disputed orders (evidence_27),
    producing a spurious tombstone fact there; no row changed because that file predates every request of that user.
    Now: PDF text is split into records (a `Record` card or a pipe-delimited row); a resource takes the identifiers of its own
    record only, and stays unresolved if several resources share the record, the record names two emails or two user ids,
    the document gives two owners, or no identifier is in the record (`docs.DOC_UNRESOLVED` lists them). Resource and display
    tokens must match whole tokens; a bare user id/email used as a display reference identifies a record only as a screenshot's
    own record field, never inside a table or list of people. A catalog link counts only from its own link time
    (`link_t` <= `audit_at`); document facts carry their capture time as observation time, so a later-captured document never
    discovers a resource or sets state or a citation. Measured on the fixed data: 2 PDF associations resolved (support_001 to
    its customer, audit_record_003 to user_03), 0 unresolved, 12 catalog links matched, 1 spurious match removed; rows,
    verdicts, steps and citations unchanged; samples as before (status 32/32, precision 1.0000, recall 0.9482, F1 0.9734).
    Tests: `code/tests/test_docs.py` (15).

15. **Adversarial, cross-component evaluation.** `code/evaluation/adversarial/` builds a synthetic dataset (15 cases, one SQL
    snapshot captured before every audit time, one ledger captured after) with hand-derived expected answers, and
    `code/tests/test_adversarial.py` (15 tests) runs the whole pipeline on it plus injected two-failure combinations:
    a later ledger contradicting a citable snapshot and discovering audit/legal records; ambiguous, missing-event, decoy and
    wide-window account events; unresolved identities beside compliant records; late-captured policy inputs; contradictory class
    vs phase; two people and resources in one document; a later-captured copy of a document; an ambiguous document owner; a
    misleading display reference. Measured: all 15 cases match their hand-derived status and steps exactly; the only cited
    file is the snapshot; no remediation or future-verification step carries a citation; every remediation answers a cited
    finding; COMPLETE rows hold only cited verifications. **Defect found and fixed:** the wide-window path settled two cases
    competing for the same nearby account event by iteration order. It now requires the event to be wanted by one case only
    (`slots`/`pipeline.assign_null_accounts`); regression tests added. Effect on the evaluation set: 2 rows changed (case_408,
    case_410, one uncorroborated account-absent step each), no verdict, finding, remediation or citation changed.
    `python code/evaluation/identity_metrics.py` separates the association classes (measured on the samples, counted only on
    the 518 evaluation requests, which have no ground truth):

    | Class | Samples (correct / total) | Evaluation set (count, unverified) |
    |---|---|---|
    | identifier-confirmed | accounts 13/13, analytics 6/6 | accounts 235, analytics 73, legal holds 8 |
    | corroborated activity | accounts 8/8, analytics 4/4 | accounts 185, analytics 65, legal holds 1 |
    | wide-window activity | accounts 6/6 | accounts 3 |
    | unresolved | 5 (none incorrect) | 91 |

    Samples: status 32/32, step precision 1.0000, recall 0.9482, F1 0.9734, 0 incorrect identity assignments. Citations over all
    550 requests: 1198 cited steps, all captured at or before `audit_at`, all citing a file that observes the resource or
    snapshots its system. Output contract: 0 violations. Not provable without ground truth: that the 185+65+3 activity-based
    associations in the evaluation set are right (only their evidence class is known), that the 91 unresolved ones would
    have been right, and recall against the hidden answers.

16. **Audit hardening: completion semantics, evidence eligibility, classification, identity time, policy, runner, cache.**
    Measured result of the whole set of changes on the unchanged dataset: `output.csv` is byte-identical to the output before
    them (518 rows, 0 verdict, finding, remediation or citation differences); samples unchanged (status 32/32, precision
    1.0000, recall 0.9482, F1 0.9734); 199 unit tests (was 81) pass; `python code/evaluation/main.py` exits 0 with all 10
    required stages passing. The changes are therefore *structural*: they close failure paths that the current data does not
    exercise (or only exercises where the previous verdict happened to be INCOMPLETE already), and they are covered by tests
    that fail under the old behaviour (checked by mutation for completion, snapshots and attribution).

    | Finding | Status | Change | Tests |
    |---|---|---|---|
    | Unknown state / unresolved condition / unresolved subject could yield COMPLETE | confirmed (unconditional DELETE used `bool(present)`; `build_case` used only `violated`) | `Treatment.uncertain`/`undetermined`; unknown profile, search, export, data-flow or restore state, unknown redact target, unresolved rule and unresolved subject all block COMPLETE; unresolved checks are planned as future verification, never as findings | `test_completion` (13) |
    | Unrelated snapshot proved absence (`mine or cands`) | confirmed | a snapshot proves absence only if it holds the subject's records, or declares `scope == "system"` in its coverage metadata, or the subject has no observation anywhere (the one shape samples case_01/02 require) | `test_snapshots` (8) |
    | Whole-PDF text used for each record; catalog email made a record "expose" an email | confirmed; the model's `identified` flag was also never read by `fact_flags` | text shown to the model is scoped to the record; exposure comes only from the record's own content (`doc_exposes` true/false/unknown); attribution stays in `email`/`user_ref` | `test_exposure` (6) |
    | Missing/invalid classification became NOT_APPLICABLE | confirmed | exact `DELETE`/`NOT_DELETE` validation, per-case retries with a fresh cache key, then `ClassificationError`; `Pipeline.run` raises on an unrecognised intent | `test_classify` (13) |
    | Identity not audit-time-aware | structural; no row of the dataset has a change or snapshot after its audit_at (checked) | `Subject.as_of(audit_at)`, `IdentityStore.resolve(email, as_of)`, `why_unresolved`; ambiguous history is excluded and listed | `test_asof_identity` (10) |
    | Retention / hold inferred from existence | the specification does not say it; it is an assumption | kept, now an explicit switch `policy.RETAIN_BY_EXISTENCE` (all 16 sample audit records without a usable date are retained in the reference answers); an UNKNOWN rule with a different action than the applicable one makes the category unresolved, one with the same action is ignored | `test_policy_semantics` (12) |
    | Missing data-flow activity written as "false" | confirmed (`docs._flags`); malformed strings were also read as inactive | `tri()` three-valued parsing in `state.py`; null/missing/malformed stays unknown | `test_dataflow` (12) |
    | Runner ignored child exit codes | confirmed | every stage's exit code, expected files, skipped tests and metric gates are checked; output of every stage is kept; the optional stage is marked | `test_eval_runner` (17) |
    | Citation checks only checked time and file coverage | confirmed | `citations.py` separates FORMAT from SUPPORT (observation of that resource in the case window, claim matches what it shows, absence needs a snapshot that speaks for the subject); `citation_check.py` and `identity_metrics.py` exit 1 on a violation; `validate_output.py` also rejects uncited findings/COMPLETE steps | `test_citations` (16) |
    | Stale index cache | confirmed | fingerprint of version, schema, parser code, OCR model, manifest and every evidence file stored in the index; mismatch rebuilds | `test_index_cache` (11) |

    Citations over all 550 requests after the stricter SUPPORT check: 1198 cited, 1198 format-valid, 1198 semantically
    supported. The same checks accept 75 of the 75 citations in the public reference answers (a calibration of the checker, not
    proof that every citation is true).

    **Conflict with the reference answers, deliberately not resolved in the strict direction.** Requiring every unattributed
    account (identity unresolved) to block COMPLETE contradicts the public samples: 4 of the 5 samples whose account could not
    be paired (case_04, 21, 25, 31) have COMPLETE reference verdicts. Measured with the switch
    `pipeline.UNRESOLVED_RESOURCE_BLOCKS_COMPLETE`: off (default) status accuracy 32/32; on 28/32, and 68 of the 153 COMPLETE
    evaluation rows would flip to INCOMPLETE. The default therefore keeps those verdicts, and the 72 COMPLETE cases that rest on
    an unattributed resource (68 evaluation, 4 sample) are listed in `Pipeline.complete_with_open_identity` instead of being
    hidden. An unresolved *subject* (request email with no single owner as of audit_at) always blocks COMPLETE; none of the 550
    requests has one.

    Two assumptions that remain assumptions: `RETAIN_BY_EXISTENCE` (above), and the account-pairing rules of item 3, which are
    validated only on identifier-confirmed groups. Model calls: the record-scoped prompts changed 4 `docfact` calls, which were
    made live (cost in `usage_report.md`).

## 6. Known limitations

- Hidden answers may include steps whose resource ids are absent from every participant file (audit-record ids above).
- Account and anonymized-analytics rows without identifiers are linked statistically through the activity stream; the
  validation above is strong but not a proof for every pool, especially pools that mix two request batches.
- Record segmentation knows two PDF layouts (`Record` cards and pipe-delimited rows); other layouts (free text, unlabeled
  tables, scanned pages) would leave resources unresolved rather than guessed. Screenshot associations rest on the catalog
  link (a screenshot may show a different person, e.g. the marketing console) and on the whole-value record fields.
- Documents and screenshots are interpreted by the model (cached); a different model snapshot could change borderline
  readings. Request classification is likewise model-based.
- Legal-hold rows are linked through logs/SQL where they exist and otherwise through activity phase; their citation falls
  back to the system's covering snapshot.
- A COMPLETE verdict can still rest on an account, analytics or legal-hold row that could not be attributed to the person
  (68 evaluation cases): see item 16 for the measured trade-off and the switch. Required-resource discovery cannot be proven
  complete from the data; unresolved identities are the only signal available.
- The no-batch snapshot path (a subject with no observation anywhere may be shown absent by the system's in-window snapshot)
  is supported by two public samples only.
- Backup restore: when a document says nothing about restore being blocked, the document prompt tells the model to answer
  "restorable", so a backup archive is assumed restorable unless the evidence blocks it. Only a null or malformed model
  answer stays unknown.
- Model-derived document flags: the model's "record absent" answer for a document is stored but never read by the state
  logic (`fact_flags`), so a document can show a record present but never absent. Only the exposure, data-flow and restore
  answers are used.
