"""Pair accounts (known only from deletion-worker activity) with cases that share the same `reported_at`.

Observed generator structure: the k cases sharing a reported_at have account-delete events in k consecutive time
slots; ordering the cases by requested_at, the slot of the j-th case is (j + r) mod k for a rotation r shared by the
group. The workflow-reported result class (COMPLETE->SUCCESS, PARTIALLY_COMPLETE->PARTIAL, NOT_STARTED->FAILED) and the
seconds-phase of a case's other deletion-worker events are independent evidence for a pairing.

A pairing is a *claim about identity*, so it is only made when the evidence separates it from the alternatives:
every candidate assignment scoring within MARGIN points of the best one is kept, and a case whose event differs among
those near-best candidates stays unresolved. Tendencies of other groups (e.g. a batch's usual rotation) are not evidence
and are never used to break a tie.
"""
import re
from datetime import datetime
from itertools import combinations, permutations

CLASS_POINTS = 10   # reported-result class matches the event's result (held for 207/207 identifier-confirmed pairs)
PHASE_POINTS = 3    # one of the case's other events agrees with the candidate account's seconds-phase (see pipeline)
# Contradiction rule: among identifier-confirmed pairs, every case that had phase-checkable events agreed with its
# own account (218/218), so disagreement of all checkable events is treated as evidence against the pairing.
# Decisive evidence = one class discrepancy (10) or two phase agreements (6): the learned phase sets cover up to ~15% of
# the 60 possible phases, so one agreement alone can happen by chance in roughly one pairing in seven, two in ~2%.
MARGIN = 2 * PHASE_POINTS
# Slot structure of a pool: the account-delete events of the k cases that share a reported_at fall one each into the k
# equal bands of [reported_at - 75 min, reported_at - 44 min]. Across all 86 identifier-confirmed pools the observed
# offsets span -74.9..-44.0 and overshoot a band edge by at most 1.1 min, so the tolerance is 2 min.
BAND_LO, BAND_HI, BAND_TOL = -75.0, -44.0, 2.0
MAX_FREE = 6        # beyond this many unpaired cases / events, enumeration is skipped and nothing is guessed


def case_key(case_id):
    """Natural sort key: case_7 < case_30 < case_100 (falls back to the raw id when it has no digits)."""
    m = re.search(r"(\d+)$", case_id)
    return (int(m.group(1)) if m else 0, case_id)


RESULT_OF_STATUS = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}


def _minutes_before_report(case, event):
    t0 = datetime.strptime(case["reported_at"], "%Y-%m-%dT%H:%M:%SZ")
    return (event["t"] - t0).total_seconds() / 60


def bands_fit(cases, events):
    """True if k events (k = number of cases) occupy the k equal bands of the pool window, one each."""
    k = len(events)
    if k != len(cases) or k == 0:
        return False
    w = (BAND_HI - BAND_LO) / k
    offs = sorted(_minutes_before_report(cases[0], e) for e in events)
    return all(BAND_LO + i * w - BAND_TOL <= o <= BAND_LO + (i + 1) * w + BAND_TOL for i, o in enumerate(offs))


def _score(case, event, bonus):
    cls = CLASS_POINTS if event["reported_result"] == RESULT_OF_STATUS[case["reported_status"]] else 0
    return cls + bonus(case["case_id"], event)


def _candidates(order, events, known, single_family, bonus):
    """All assignments the evidence structure allows, as (score, {case_id: event})."""
    k = len(order)
    out = []

    def consistent(assign):
        return all(assign.get(cid, {}).get("resource_id", rid) == rid for cid, rid in known.items())

    if single_family and k <= MAX_FREE and len(events) >= k and len(events) <= MAX_FREE + 2:
        subsets = [events] if len(events) == k else list(combinations(events, k))
        for sub in subsets:                       # events beyond k belong to people outside the request list
            if not bands_fit(order, sub):         # an event that does not fit the pool's slot structure is not its own
                continue
            for r in range(k):
                assign = {c["case_id"]: sub[(j + r) % k] for j, c in enumerate(order)}
                if consistent(assign):
                    out.append((sum(_score(c, assign[c["case_id"]], bonus) for c in order), assign))
        return out                                # structure applies: no generic fallback that would ignore it
    free = [c for c in order if c["case_id"] not in known]
    used = set(known.values())
    free_events = [e for e in events if e["resource_id"] not in used]
    if free and len(free) <= MAX_FREE and len(free_events) <= MAX_FREE + 2 and len(free_events) >= len(free):
        for perm in permutations(free_events, len(free)):
            assign = {c["case_id"]: e for c, e in zip(free, perm)}
            out.append((sum(_score(c, e, bonus) for c, e in zip(free, perm)), assign))
    return out


def solve_group(cases, events, known=None, single_family=True, bonus=lambda cid, ev: 0, checkable=None,
                require_evidence=True):
    """cases: request dicts of the pool; events: account-delete events; known: {case_id: resource_id} fixed already.

    Returns (assigned {case_id: resource_id}, unresolved {case_id: {"reason", "candidates"}}) for the cases not in
    `known`. A case is assigned only if every near-best candidate assignment gives it the same event and its result class
    is not contradicted without independent (phase) support. `checkable(case_id)` is how many of the case's other
    events can be phase-checked; if any can but none agrees with the chosen account, the independent evidence
    contradicts the pairing and the case stays unresolved."""
    known = known or {}
    events = sorted(events, key=lambda e: e["t"])
    order = sorted(cases, key=lambda c: (c["requested_at"], case_key(c["case_id"])))
    free = [c for c in order if c["case_id"] not in known]
    cands = _candidates(order, events, known, single_family, bonus)
    if not cands:
        why = ("no account-delete event in the pool window" if not events else
               "fewer account events than cases: a true event is missing" if len(events) < len(free) else
               "the pool's events do not fit its slot structure (a decoy or a missing event)" if single_family else
               "no candidate pairing could be enumerated")
        return {}, {c["case_id"]: {"reason": why, "candidates": []} for c in free}
    best = max(s for s, _ in cands)
    near = [a for s, a in cands if best - s < MARGIN]
    assigned, unresolved = {}, {}
    for c in free:
        cid = c["case_id"]
        options = {a[cid]["resource_id"] for a in near if cid in a}
        if len(options) != 1:
            alts = sorted({a[cid]["resource_id"] for s, a in cands if cid in a and best - s < MARGIN + CLASS_POINTS})
            unresolved[cid] = {"reason": "candidates are not separated by at least %d points" % MARGIN, "candidates": alts}
            continue
        rid = next(iter(options))
        ev = next(a[cid] for a in near if cid in a)
        n_check = checkable(cid) if checkable else 0
        if n_check > 0 and bonus(cid, ev) < n_check * PHASE_POINTS:
            # among identifier-confirmed pairs 215 of 218 agree on ALL of the case's checkable events, so a partial or
            # empty agreement is evidence against the pairing
            unresolved[cid] = {"reason": "not all of the case's other events agree with this account's phase", "candidates": [rid]}
            continue
        if ev["reported_result"] != RESULT_OF_STATUS[c["reported_status"]] and bonus(cid, ev) == 0:
            unresolved[cid] = {"reason": "reported-result class contradicts the only candidate", "candidates": [rid]}
            continue
        # corroboration: a lone best score is not identity. The result class is a workflow claim shared by many events, and
        # the slot bands only show that an event belongs to the pool, so the pairing also needs the case's OWN independent
        # evidence: at least one of its other activity events agreeing with the account's seconds-phase.
        if require_evidence and bonus(cid, ev) <= 0:   # False only for corroboration analyses in evaluation/
            unresolved[cid] = {"reason": "no independent evidence beyond the result class", "candidates": [rid]}
            continue
        assigned[cid] = rid
    return assigned, unresolved
