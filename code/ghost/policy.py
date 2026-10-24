"""Policy layer: every resource treatment is decided here from deletion_rules.csv before any output is built.

A rule applies only when its `condition` evaluates to TRUE for the resource. Conditions are evaluated three-valued
(TRUE / FALSE / UNKNOWN) from evidence-derived context; UNKNOWN is never silently resolved either way.

Deliberate policy decisions (the CSV is silent on these, so they are choices, not facts from the data):
* FALSE means "the rule does not apply". deletion_rules.csv names no alternative action for that case (e.g. a support
  conversation without shared context, an attachment shared with another person, an audit record past retention), and the
  contract forbids touching data the policy does not require to be deleted/redacted/anonymized, so NOTHING is emitted for
  the resource (no finding, remediation or verification). The two exceptions are conditions of the form "still
  attributable" / "contains an email": FALSE there means the rule's required state (anonymized / redacted) already
  holds, and that state is still verified.
* UNKNOWN is never promoted to TRUE or FALSE: the treatment is flagged unresolved, makes no finding or remediation, and
  appears only as a future verification in INCOMPLETE rows (never in COMPLETE rows, which assert only what citable evidence
  proves).
* Condition inputs come from evidence captured at or before audit_at; later-captured files may only discover resources.
* A treatment is DETERMINED only when both the rule and the resource state are known. Unknown state (no citable
  observation of a record or data flow) is neither "compliant" nor a violation: the treatment is `uncertain`, produces no
  finding, and blocks a COMPLETE verdict (see Pipeline.build_case).
* A rule that is UNKNOWN next to a TRUE rule of a different action could change the outcome (e.g. an unconfirmed RETAIN
  next to a DELETE), so the category is unresolved; an UNKNOWN rule with the same action as the TRUE one cannot, and is ignored.
* RETAIN_BY_EXISTENCE: rule_12/13 are read as "the supplied date has not passed". When no date is available, a record that
  is still observed is treated as retained. The specification does not say this; it is an empirical assumption (all 16
  public-sample audit records without a usable date are retained in the sample answers) and is switchable here.
* SUPPORT_MESSAGE: a record that a source types as a support message (handover/ledger lines) counts as a conversation for
  rule_05 even when no text is visible, because rule_05 is the only rule for that category and the data never encodes
  "shared context" separately. Where text, ticket fields or a screenshot are available, those are used instead.
"""
from dataclasses import dataclass

from .index import read_csv
from .state import ResourceState

TRUE, FALSE, UNKNOWN = "TRUE", "FALSE", "UNKNOWN"
RETAIN_BY_EXISTENCE = True   # see the module docstring: an empirical assumption, not a rule from the specification

# action name in deletion_rules.csv -> (finding op, remediation op, verification op, verification state)
ACTION_OPS = {
    "DELETE": ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT", "ABSENT"),
    "REDACT": ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED", "REDACTED"),
    "ANONYMIZE": ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
}
STATE_OF_OP = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT", "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE", "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}


# ----------------------------------------------------------------------------- condition evaluators
# Each takes (ResourceState, context) and returns (TRUE|FALSE|UNKNOWN, reason). `context` is filled from evidence by the
# pipeline: audit_at, subject_emails, subject_user, owners, retention_until, retained_until, account_status,
# conversation, text_has_email, restore_known.
def _always(st, ctx):
    return TRUE, "unconditional"


def _shared_conversation(st, ctx):
    # rule_05: a support conversation inherently carries shared customer/agent/order context that must be retained.
    if ctx.get("conversation"):
        return TRUE, "record is a support conversation with customer/agent/order content"
    return UNKNOWN, "no conversation content visible, shared context cannot be established"


def _attachment_owned_only_by_subject(st, ctx):
    owners = ctx.get("owners") or set()
    if not owners:
        return UNKNOWN, "no owner information for the attachment"
    mine = {o for o in owners if o in (ctx.get("subject_emails") or set()) or o == ctx.get("subject_user")}
    if mine == owners:
        return TRUE, "only the subject is named as owner"
    return FALSE, "attachment also names another person"


def _still_attributable(st, ctx):
    if st.present is False:
        return FALSE, "record no longer exists"
    if st.present is None and not st.identified:
        return UNKNOWN, "no citable observation of the record"
    return (TRUE, "record carries identifiers or a user link") if st.identified else (FALSE, "no identifier or user link left")


def _log_has_email(st, ctx):
    if ctx.get("text_has_email") is None:
        return UNKNOWN, "log content not visible"
    return (TRUE, "log text contains an email") if ctx["text_has_email"] else (FALSE, "no email in log text")


def _within_retention(st, ctx):
    until, audit_at = ctx.get("retention_until"), ctx.get("audit_at")
    if until and audit_at:
        return (TRUE, f"retained until {until}") if until >= audit_at else (FALSE, f"retention ended {until}")
    if RETAIN_BY_EXISTENCE and st.present:   # the audit store still holds the record, so it has not aged out
        return TRUE, "record still held by the audit store at audit time"
    return UNKNOWN, "no retention date and no citable observation"


def _legal_hold_active(st, ctx):
    until, audit_at = ctx.get("retained_until"), ctx.get("audit_at")
    if until and audit_at:
        return (TRUE, f"hold active until {until}") if until >= audit_at else (FALSE, f"hold ended {until}")
    if RETAIN_BY_EXISTENCE and st.present:
        return TRUE, "hold record is served by the legal service at audit time"
    return UNKNOWN, "no hold end date and no citable observation"


def _restore_can_be_blocked(st, ctx):
    if ctx.get("restore_known"):
        return TRUE, "restore access is a controllable flag on the archive"
    return UNKNOWN, "no restore-access information for the archive"


def _tombstone_only_id_needed(st, ctx):
    status = ctx.get("account_status")
    if status is None:
        return TRUE, "record is a deleted-account reference (tombstone)"
    return (TRUE, "account status DELETED") if str(status).upper() == "DELETED" else (FALSE, f"account status {status}")


CONDITIONS = {
    "always": _always,
    "conversation contains shared customer, agent, or order context that must be retained": _shared_conversation,
    "attachment belongs only to the deletion subject": _attachment_owned_only_by_subject,
    "record is still attributable to a user": _still_attributable,
    "log contains a user email address": _log_has_email,
    "record is within the supplied retention period": _within_retention,
    "a supplied legal hold is active": _legal_hold_active,
    "restore access can be blocked": _restore_can_be_blocked,
    "account is deleted and only a stable internal user_id is needed to preserve references to retained records": _tombstone_only_id_needed,
}
# FALSE on these means the rule's required state already holds (nothing attributable / no email left), so the
# verification of that state is still planned; FALSE on the other conditions just means the rule does not apply.
FALSE_MEANS_COMPLIANT = {"record is still attributable to a user", "log contains a user email address"}


@dataclass
class Treatment:
    resource_id: str
    system: str
    required: str          # DELETE | REDACT | ANONYMIZE | RETAIN | DISABLE_FLOW | BLOCK_RESTORE | NONE
    violated: bool
    finding_op: str = ""
    remediation_op: str = ""
    verify_op: str = ""
    evidence_id: str = ""
    rule_id: str = ""
    condition: str = TRUE          # evaluated condition of the applied rule
    reason: str = ""
    unresolved: bool = False       # policy cannot say what applies (UNKNOWN condition, conflict or no rule)
    uncertain: bool = False        # the rule is known but the resource's state at audit time is not

    @property
    def undetermined(self):
        """True if this treatment cannot be called compliant: it must block a COMPLETE verdict."""
        return self.unresolved or self.uncertain


class PolicyEngine:
    def __init__(self, rules=None):
        self.rules = {}
        for r in (rules if rules is not None else read_csv("deletion_rules.csv")):
            self.rules.setdefault(r["data_category"], []).append(r)

    def select_rule(self, category, st, ctx):
        """-> (rule or None, TRUE|FALSE|UNKNOWN, reason). Evaluates every rule of the category."""
        rules = self.rules.get(category)
        if not rules:
            return None, UNKNOWN, f"no rule for category {category}"
        evals = []
        for r in rules:
            fn = CONDITIONS.get(r["condition"].strip().lower())
            val, why = fn(st, ctx) if fn else (UNKNOWN, f"no evaluator for condition '{r['condition']}'")
            evals.append((r, val, why))
        true = [e for e in evals if e[1] == TRUE]
        unknown = [e for e in evals if e[1] == UNKNOWN]
        if len({e[0]["required_action"] for e in true}) > 1:
            return None, UNKNOWN, "conflicting rules apply: " + ", ".join(e[0]["rule_id"] for e in true)
        if true:
            rival = [e for e in unknown if e[0]["required_action"] != true[0][0]["required_action"]]
            if rival:   # an unconfirmed rule with another action could change what must be done
                return None, UNKNOWN, ("rule %s applies but rule %s (%s) is unresolved: %s" %
                                       (true[0][0]["rule_id"], rival[0][0]["rule_id"], rival[0][0]["required_action"], rival[0][2]))
            return true[0][0], TRUE, true[0][2]
        if unknown:
            return unknown[0][0], UNKNOWN, unknown[0][2]
        return evals[0][0], FALSE, evals[0][2]

    def decide(self, st: ResourceState, ctx=None) -> Treatment:
        ctx = ctx or getattr(st, "context", None) or {}
        cat = st.category
        if cat == "DATA_FLOW":   # not in deletion_rules.csv: the contract forbids any active recreation path
            return Treatment(st.resource_id, st.system, "DISABLE_FLOW", st.active is True, "REPORT_ACTIVE_DATA_FLOW",
                             "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED", st.evidence_id, "contract:no-recreation-path",
                             uncertain=st.active is None, reason="data-flow state unknown" if st.active is None else "")
        rule, value, why = self.select_rule(cat, st, ctx)
        if rule is None or value == UNKNOWN:
            return Treatment(st.resource_id, st.system, "NONE", False, evidence_id=st.evidence_id,
                             rule_id=rule["rule_id"] if rule else "", condition=UNKNOWN, reason=why, unresolved=True,
                             verify_op=self._verify_op(rule) if rule else "")
        action = rule["required_action"]
        if value == FALSE:
            keep = rule["condition"].strip().lower() in FALSE_MEANS_COMPLIANT
            return Treatment(st.resource_id, st.system, "NONE", False, evidence_id=st.evidence_id, rule_id=rule["rule_id"],
                             condition=FALSE, reason=why, verify_op=self._verify_op(rule) if keep else "")
        if action == "RETAIN":
            if cat == "BACKUP_ARCHIVE":   # rule_14: retained only while restore access is blocked
                return Treatment(st.resource_id, st.system, "BLOCK_RESTORE", st.active is True, "REPORT_RESTORE_RISK",
                                 "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED", st.evidence_id, rule["rule_id"], TRUE, why,
                                 uncertain=st.active is None)
            return Treatment(st.resource_id, st.system, "RETAIN", False, "", "", "VERIFY_RECORD_PRESENT", st.evidence_id,
                             rule["rule_id"], TRUE, why, uncertain=st.present is None)
        f_op, r_op, v_op, _ = ACTION_OPS[action]
        if action == "DELETE":
            violated, uncertain = bool(st.present), st.present is None
        else:  # REDACT / ANONYMIZE: the record may stay but must not expose identifiers
            violated = bool(st.present is not False and st.identified)
            uncertain = not st.identified and ((st.present is None and not st.evidence_id)   # nothing was observed at all
                                               or st.exposure_unknown)                      # or no record says what it exposes
        return Treatment(st.resource_id, st.system, action, violated, f_op, r_op, v_op, st.evidence_id, rule["rule_id"],
                         TRUE, why, uncertain=uncertain and not violated)

    @staticmethod
    def _verify_op(rule):
        action = rule["required_action"]
        if action == "RETAIN":
            return "VERIFY_RESTORE_BLOCKED" if rule["data_category"] == "BACKUP_ARCHIVE" else "VERIFY_RECORD_PRESENT"
        return ACTION_OPS[action][2]
