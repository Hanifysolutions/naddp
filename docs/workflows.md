# Workflows — State Machine Contract

> This is the **binding specification** for the three workflow state machines in `BUILD_BIBLE.md` §9.
> A later track implements it in `apps/api/app/domain/` (transition tables) and
> `apps/api/app/services/` (execution). Route handlers contain no transition logic.
>
> Every table below is normative. If the implementation disagrees with this document, the
> implementation is wrong — or this document is superseded by an explicit edit, not by drift.

---

## 0. Rules that apply to all three machines

1. **Deny-by-default and table-driven.** A transition is legal only if `(from_state, event)` appears in
   that machine's table. Anything else raises `IllegalTransition` and returns HTTP 409. There is no
   "set the state to X" endpoint — only events.
2. **Server-validated.** The client sends an event, never a target state. The server computes the
   target from the table. A client that proposes a state is ignored.
3. **Permission is checked before the transition is attempted.** The `Required permission` column is a
   `require(...)` dependency (ADR-0003). A denial writes an audit row with `policy_result = DENY` and
   does **not** change state.
4. **Every transition writes exactly one `audit_events` row**, in the same transaction as the state
   change (ADR-0004). If the audit write fails, the transition rolls back. The `Audit action` column
   gives the exact `action` value — these strings are a closed vocabulary in
   `app/audit/actions.py` and must match character for character.
5. **Audit `detail` shape for a transition** is fixed:
   ```json
   { "from_state": "...", "to_state": "...", "event": "...", "reason": "...|null", "trace_id": "...|null" }
   ```
   `trace_id` is present when an AI artefact informed the transition; `reason` is present when the
   table marks it required.
6. **Terminal states accept no events.** Every attempt raises `IllegalTransition`. Terminal states are
   marked ⏹ and listed explicitly per machine. Recovery from a terminal state means creating a **new**
   object that references the old one — never resurrecting it.
7. **No transition is autonomous.** Every event has an authenticated human actor
   (`BUILD_BIBLE.md` §6). The AI Gateway may produce a *proposal* carrying
   `approval_status = PENDING_APPROVAL`; a proposal is data, not an event. The state machine only
   advances when a human with the listed permission fires the event.
8. **Idempotency.** Firing an event that is already reflected in the current state (for example
   `approve` on an already-`APPROVED` follow-up) is a no-op returning 200 with the current state, and
   writes **no** second audit row. It is not an error, and it is not a duplicate transition.
9. **Optimistic concurrency.** Every transition request carries the object's current `version`; a
   mismatch returns 409 without changing state. Two officers cannot race a case into two states.
10. **Reason text is never free-form into the audit log unbounded.** Where `reason` is required it is
    capped at 500 characters, stored in the object's own reason column, and referenced (not copied in
    full) in the audit `detail`.

Legend: ⏹ terminal · ⚠ non-autonomous control per `BUILD_BIBLE.md` §6 · ✎ requires `reason`

---

## 1. Opportunity

**Object:** `opportunities` · **Context:** `opportunities` · **`object_type`:** `opportunities.opportunity`

`BUILD_BIBLE.md` §9: `DETECTED → QUALIFIED → CONTACT_PLANNED → CONTACTED → MEETING → NEGOTIATION → PARTNERED/CLOSED`

### States

| State | Meaning | Terminal |
|---|---|---|
| `DETECTED` | Created from an intelligence signal. Not yet judged worth pursuing. Initial state; the only state an opportunity may be created in. | |
| `QUALIFIED` | A human has judged it real and in scope. Scoring is explainable and editable from here on. | |
| `CONTACT_PLANNED` | An approach has been decided: who contacts whom, and with what ask. | |
| `CONTACTED` | The approach has been made and recorded as an interaction. | |
| `MEETING` | A meeting is scheduled or has occurred; the opportunity is live with a counterpart. | |
| `NEGOTIATION` | Substantive terms are under discussion. Content here is typically `CONFIDENTIAL` (ADR-0006). | |
| `PARTNERED` | A partnership has been concluded. Success terminal. | ⏹ |
| `CLOSED` | Not proceeding — dismissed, lapsed, lost, or superseded. Single non-success terminal; always carries a reason. | ⏹ |

### Transitions

| # | From | Event | To | Required permission | Audit action | Notes |
|---|---|---|---|---|---|---|
| 1 | *(none)* | `detect` | `DETECTED` | `create:opportunity` | `opportunity.detected` | Creation. `detail.source_signal_id` required. |
| 2 | `DETECTED` | `qualify` | `QUALIFIED` | `qualify:opportunity` | `opportunity.qualified` | Requires a non-null score and at least one evidence reference. |
| 3 | `DETECTED` | `dismiss` | `CLOSED` ⏹ | `close:opportunity` | `opportunity.closed` | ✎ `close_reason` required. |
| 4 | `QUALIFIED` | `plan_contact` | `CONTACT_PLANNED` | `advance:opportunity` | `opportunity.contact_planned` | Requires a linked `stakeholders.stakeholder`. |
| 5 | `QUALIFIED` | `close` | `CLOSED` ⏹ | `close:opportunity` | `opportunity.closed` | ✎ |
| 6 | `CONTACT_PLANNED` | `record_contact` | `CONTACTED` | `advance:opportunity` | `opportunity.contacted` | Requires a linked `stakeholders.interaction`. |
| 7 | `CONTACT_PLANNED` | `close` | `CLOSED` ⏹ | `close:opportunity` | `opportunity.closed` | ✎ |
| 8 | `CONTACTED` | `schedule_meeting` | `MEETING` | `advance:opportunity` | `opportunity.meeting_scheduled` | Requires a linked `meetings.meeting`. |
| 9 | `CONTACTED` | `close` | `CLOSED` ⏹ | `close:opportunity` | `opportunity.closed` | ✎ |
| 10 | `MEETING` | `enter_negotiation` | `NEGOTIATION` | `advance:opportunity` | `opportunity.negotiation_opened` | Classification is raised to at least `CONFIDENTIAL` on entry. |
| 11 | `MEETING` | `close` | `CLOSED` ⏹ | `close:opportunity` | `opportunity.closed` | ✎ |
| 12 | `NEGOTIATION` | `partner` | `PARTNERED` ⏹ | `commit:opportunity` ⚠ | `opportunity.partnered` | **§6 control — a commitment.** `AMBASSADOR` or `DEPUTY` only. Never AI-initiated. |
| 13 | `NEGOTIATION` | `close` | `CLOSED` ⏹ | `close:opportunity` | `opportunity.closed` | ✎ |
| 14 | `QUALIFIED`, `CONTACT_PLANNED`, `CONTACTED`, `MEETING`, `NEGOTIATION` | `revert` | *immediately preceding state* | `revert:opportunity` | `opportunity.reverted` | ✎ Corrects a mis-advance. One step only, never across a terminal. `DEPUTY` / `AMBASSADOR` only, reason required, and the reason reaches the audit row's payload. Q-04 **RESOLVED — approved**. |

Terminal states: `PARTNERED`, `CLOSED`. Neither is reopenable. A revived opportunity is a new
`DETECTED` row carrying `superseded_opportunity_id`.

### Permission grants

| Permission | AMBASSADOR | DEPUTY | TRADE_OFFICER | CONSULAR_OFFICER | DIASPORA_OFFICER | ADMIN |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `create:opportunity` | ✔ | ✔ | ✔ | — | — | — |
| `qualify:opportunity` | ✔ | ✔ | ✔ | — | — | — |
| `advance:opportunity` | ✔ | ✔ | ✔ | — | — | — |
| `close:opportunity` | ✔ | ✔ | ✔ | — | — | — |
| `commit:opportunity` ⚠ | ✔ | ✔ | — | — | — | — |
| `revert:opportunity` | ✔ | ✔ | — | — | — | — |

### Diagram

```mermaid
stateDiagram-v2
    [*] --> DETECTED : detect
    DETECTED --> QUALIFIED : qualify
    QUALIFIED --> CONTACT_PLANNED : plan_contact
    CONTACT_PLANNED --> CONTACTED : record_contact
    CONTACTED --> MEETING : schedule_meeting
    MEETING --> NEGOTIATION : enter_negotiation
    NEGOTIATION --> PARTNERED : partner (AMBASSADOR/DEPUTY)

    DETECTED --> CLOSED : dismiss
    QUALIFIED --> CLOSED : close
    CONTACT_PLANNED --> CLOSED : close
    CONTACTED --> CLOSED : close
    MEETING --> CLOSED : close
    NEGOTIATION --> CLOSED : close

    QUALIFIED --> DETECTED : revert
    CONTACT_PLANNED --> QUALIFIED : revert
    CONTACTED --> CONTACT_PLANNED : revert
    MEETING --> CONTACTED : revert
    NEGOTIATION --> MEETING : revert

    PARTNERED --> [*]
    CLOSED --> [*]

    note right of PARTNERED : Terminal. Commitment control (§6).
    note right of CLOSED : Terminal. Reason always required.
```

---

## 2. Meeting follow-up

**Object:** `actions` (follow-up artefact attached to a `meetings.meeting`) · **Context:** `meetings` ·
**`object_type`:** `meetings.followup`

`BUILD_BIBLE.md` §9: `DRAFTED → OFFICER_REVIEW → APPROVED → SENT`

This is **winning moment #2** — "AI drafts, humans decide". The block on `send` must be visible in the
UI, and the reason for the block must be legible: *this action requires approval by a second person
holding `approve:meeting_followup`.*

### States

| State | Meaning | Terminal |
|---|---|---|
| `DRAFTED` | A draft exists. Produced by the Gateway purpose `meeting_followup` with `approval_status = PENDING_APPROVAL`, or written by hand. Freely editable. Initial state. | |
| `OFFICER_REVIEW` | Submitted for approval. **Locked for editing** — the artefact an approver sees is the artefact that gets sent. Any edit requires returning to `DRAFTED`. | |
| `APPROVED` | A human other than the drafter has approved it. Sending is now permitted. Still not sent. | |
| `SENT` | Dispatched to the external recipient. Success terminal; content immutable thereafter. | ⏹ |
| `DISCARDED` | Abandoned without sending. Non-success terminal. **Proposed addition** beyond §9 — see `docs/OPEN_QUESTIONS.md` Q-05. | ⏹ |

### Transitions

| # | From | Event | To | Required permission | Audit action | Notes |
|---|---|---|---|---|---|---|
| 1 | *(none)* | `draft` | `DRAFTED` | `draft:meeting_followup` | `meeting_followup.drafted` | `detail.trace_id` required when AI-generated. Records `drafted_by`. |
| 2 | `DRAFTED` | `edit` | `DRAFTED` | `draft:meeting_followup` | `meeting_followup.edited` | Self-transition. Audited because the content of an outbound communication changed. |
| 3 | `DRAFTED` | `submit_for_review` | `OFFICER_REVIEW` | `submit:meeting_followup` | `meeting_followup.submitted` | Recipient list and subject must be non-empty. Content locks. |
| 4 | `DRAFTED` | `discard` | `DISCARDED` ⏹ | `discard:meeting_followup` | `meeting_followup.discarded` | ✎ |
| 5 | `OFFICER_REVIEW` | `approve` | `APPROVED` | `approve:meeting_followup` ⚠ | `meeting_followup.approved` | **§6 control — external outreach.** Approver **must not** be the drafter (separation of duties, enforced server-side). `AMBASSADOR` / `DEPUTY` only. |
| 6 | `OFFICER_REVIEW` | `request_changes` | `DRAFTED` | `approve:meeting_followup` | `meeting_followup.changes_requested` | ✎ Rejection path. Content unlocks. |
| 7 | `OFFICER_REVIEW` | `discard` | `DISCARDED` ⏹ | `discard:meeting_followup` | `meeting_followup.discarded` | ✎ |
| 8 | `APPROVED` | `send` | `SENT` ⏹ | `send:meeting_followup` ⚠ | `meeting_followup.sent` | **§6 control — diplomatic communication.** Legal **only** from `APPROVED`. `detail.recipient_count` required. |
| 9 | `APPROVED` | `revoke_approval` | `DRAFTED` | `approve:meeting_followup` | `meeting_followup.approval_revoked` | ✎ Withdraws approval before dispatch. Content unlocks. |
| 10 | `APPROVED` | `discard` | `DISCARDED` ⏹ | `discard:meeting_followup` | `meeting_followup.discarded` | ✎ |

Terminal states: `SENT`, `DISCARDED`. A follow-up to a sent follow-up is a new artefact.

**The load-bearing invariant:** `SENT` is reachable from `APPROVED` and from nowhere else. There is no
event, no permission, no administrative path, and no AI purpose that produces `SENT` from any other
state. A test asserts this directly over the transition table.

### Permission grants

| Permission | AMBASSADOR | DEPUTY | TRADE_OFFICER | CONSULAR_OFFICER | DIASPORA_OFFICER | ADMIN |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `draft:meeting_followup` | ✔ | ✔ | ✔ | ✔ | ✔ | — |
| `submit:meeting_followup` | ✔ | ✔ | ✔ | ✔ | ✔ | — |
| `approve:meeting_followup` ⚠ | ✔ | ✔ | — | — | — | — |
| `send:meeting_followup` ⚠ | ✔ | ✔ | ✔ | ✔ | ✔ | — |
| `discard:meeting_followup` | ✔ | ✔ | ✔ | ✔ | ✔ | — |

`send` is deliberately widely held: the officer who drafted it may dispatch it, but only after someone
senior has approved it. Holding `send` without an `APPROVED` artefact achieves nothing. This is a
sharper demonstration than restricting `send` would be — the block is structural, not merely a
permission the demo persona happens to lack.

### Diagram

```mermaid
stateDiagram-v2
    [*] --> DRAFTED : draft (AI or human)
    DRAFTED --> DRAFTED : edit
    DRAFTED --> OFFICER_REVIEW : submit_for_review
    OFFICER_REVIEW --> APPROVED : approve (≠ drafter, AMBASSADOR/DEPUTY)
    OFFICER_REVIEW --> DRAFTED : request_changes
    APPROVED --> SENT : send
    APPROVED --> DRAFTED : revoke_approval

    DRAFTED --> DISCARDED : discard
    OFFICER_REVIEW --> DISCARDED : discard
    APPROVED --> DISCARDED : discard

    SENT --> [*]
    DISCARDED --> [*]

    note right of OFFICER_REVIEW : Content locked while under review.
    note right of SENT : Terminal. Reachable ONLY from APPROVED.
```

---

## 3. Consular case

**Object:** `cases` · **Context:** `consular` · **`object_type`:** `consular.case`

`BUILD_BIBLE.md` §9: `NEW → TRIAGED → ASSIGNED → AWAITING_CITIZEN/IN_REVIEW → ESCALATED → RESOLVED → CLOSED`

Highest-sensitivity machine in the system. Default classification `CONSULAR_SENSITIVE` (ADR-0006), and
**no consular determination may be autonomous** (`BUILD_BIBLE.md` §6). The Gateway purpose
`consular_triage` produces a *proposed* triage — a case type, a priority and a rationale, with
`approval_status = PENDING_APPROVAL`. It never fires an event.

Every transition additionally appends an immutable `case_events` row (the citizen-visible-in-summary
case timeline) as well as the `audit_events` row (the governance log). Both are written in the same
transaction; `case_events` carries no content the citizen may not see.

### States

| State | Meaning | SLA clock | Terminal |
|---|---|---|---|
| `NEW` | Received, not yet assessed. Initial state. | running | |
| `TRIAGED` | A human has confirmed the case type, priority and classification. | running | |
| `ASSIGNED` | Allocated to a named consular officer. | running | |
| `AWAITING_CITIZEN` | Blocked pending information or documents from the citizen. | **paused** | |
| `IN_REVIEW` | Actively being worked, or under determination by the assigned officer. | running | |
| `ESCALATED` | Raised for a decision above the assigned officer — complexity, sensitivity, SLA breach risk, or a welfare/detention matter. | running | |
| `RESOLVED` | A determination has been made and communicated. Not yet closed; reopenable. | stopped | |
| `CLOSED` | Administratively complete. Single terminal state, always with a reason (resolved, withdrawn, duplicate, out of jurisdiction, referred). | stopped | ⏹ |

`default_sla_days` per case type comes from `data/taxonomy/consular_case_types.json`. The SLA clock
pauses in `AWAITING_CITIZEN` because delay attributable to the citizen must not count against the
mission's service standard, and the paused duration is recorded so the dashboard's ageing view can
show both elapsed and chargeable time.

### Transitions

| # | From | Event | To | Required permission | Audit action | Notes |
|---|---|---|---|---|---|---|
| 1 | *(none)* | `intake` | `NEW` | `create:consular_case` | `case.created` | Mints `public_ref` (ADR-0007). Classification set to the case type's default. |
| 2 | `NEW` | `triage` | `TRIAGED` | `triage:consular_case` ⚠ | `case.triaged` | **§6 control — a determination.** Human confirms case type, priority, classification. If an AI proposal informed it, `detail.trace_id` is required and the proposal's `approval_status` moves to `APPROVED`. |
| 3 | `NEW` | `close` | `CLOSED` ⏹ | `close:consular_case` ⚠ | `case.closed` | ✎ Duplicate, out of jurisdiction, or withdrawn before triage. |
| 4 | `TRIAGED` | `assign` | `ASSIGNED` | `assign:consular_case` | `case.assigned` | `detail.assignee_id` required. |
| 5 | `TRIAGED` | `close` | `CLOSED` ⏹ | `close:consular_case` ⚠ | `case.closed` | ✎ |
| 6 | `ASSIGNED` | `request_information` | `AWAITING_CITIZEN` | `work:consular_case` | `case.information_requested` | ✎ Pauses the SLA clock. `detail.requested_items` required. |
| 7 | `ASSIGNED` | `begin_review` | `IN_REVIEW` | `work:consular_case` | `case.review_started` | |
| 8 | `ASSIGNED` | `escalate` | `ESCALATED` | `escalate:consular_case` | `case.escalated` | ✎ |
| 9 | `ASSIGNED` | `reassign` | `ASSIGNED` | `assign:consular_case` | `case.reassigned` | ✎ Self-transition. `detail.from_assignee_id` and `detail.assignee_id` required. |
| 10 | `AWAITING_CITIZEN` | `information_received` | `IN_REVIEW` | `work:consular_case` | `case.information_received` | Resumes the SLA clock; records paused duration. |
| 11 | `AWAITING_CITIZEN` | `escalate` | `ESCALATED` | `escalate:consular_case` | `case.escalated` | ✎ |
| 12 | `AWAITING_CITIZEN` | `close` | `CLOSED` ⏹ | `close:consular_case` ⚠ | `case.closed` | ✎ Non-response after the configured lapse period. Requires a recorded prior reminder. |
| 13 | `IN_REVIEW` | `request_information` | `AWAITING_CITIZEN` | `work:consular_case` | `case.information_requested` | ✎ Pauses the SLA clock. |
| 14 | `IN_REVIEW` | `escalate` | `ESCALATED` | `escalate:consular_case` | `case.escalated` | ✎ |
| 15 | `IN_REVIEW` | `resolve` | `RESOLVED` | `resolve:consular_case` ⚠ | `case.resolved` | **§6 control — a determination.** ✎ `detail.determination` and `detail.communicated_at` required. Never AI-initiated. |
| 16 | `ESCALATED` | `return_to_officer` | `IN_REVIEW` | `escalate:consular_case` | `case.de_escalated` | ✎ Guidance given; the assigned officer resumes. |
| 17 | `ESCALATED` | `request_information` | `AWAITING_CITIZEN` | `work:consular_case` | `case.information_requested` | ✎ Pauses the SLA clock. |
| 18 | `ESCALATED` | `resolve` | `RESOLVED` | `resolve:consular_case` ⚠ | `case.resolved` | **§6 control.** ✎ Resolution at the escalated level. |
| 19 | `ESCALATED` | `close` | `CLOSED` ⏹ | `close:consular_case` ⚠ | `case.closed` | ✎ |
| 20 | `RESOLVED` | `reopen` | `IN_REVIEW` | `reopen:consular_case` | `case.reopened` | ✎ New information or a challenge to the determination. Restarts the SLA clock with a fresh budget; the original is retained. |
| 21 | `RESOLVED` | `close` | `CLOSED` ⏹ | `close:consular_case` ⚠ | `case.closed` | **§6 control — case closure.** ✎ The expected happy-path terminal. |

Terminal state: `CLOSED` — the only one. A closed case is never reopened; a subsequent matter is a new
case carrying `related_case_id`. This is deliberate: reopening a closed consular case would make the
case timeline non-monotonic, and the timeline is the record a citizen and an auditor rely on.

**Unreachable-by-design:** there is no path from `NEW` or `TRIAGED` directly to `RESOLVED`. A
determination requires an assigned, accountable officer.

### Permission grants

| Permission | AMBASSADOR | DEPUTY | TRADE_OFFICER | CONSULAR_OFFICER | DIASPORA_OFFICER | ADMIN |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `create:consular_case` | — | — | — | ✔ | — | — |
| `triage:consular_case` ⚠ | — | ✔ | — | ✔ | — | — |
| `assign:consular_case` | — | ✔ | — | ✔ | — | — |
| `work:consular_case` | — | ✔ | — | ✔ | — | — |
| `escalate:consular_case` | ✔ | ✔ | — | ✔ | — | — |
| `resolve:consular_case` ⚠ | — | ✔ | — | ✔ | — | — |
| `close:consular_case` ⚠ | — | ✔ | — | ✔ | — | — |
| `reopen:consular_case` | — | ✔ | — | ✔ | — | — |
| `read:consular_case` | ✔ | ✔ | — | ✔ | — | — |

`TRADE_OFFICER`, `DIASPORA_OFFICER` and `ADMIN` hold **no** consular permission and lack the `consular`
compartment (ADR-0006), so they cannot read a case, let alone transition one. `AMBASSADOR` can read
and escalate but does not routinely triage or determine — head-of-mission oversight, not casework.
This is the pairing exercised by `evals/security/prompt_injection.example.jsonl`.

### Diagram

```mermaid
stateDiagram-v2
    [*] --> NEW : intake
    NEW --> TRIAGED : triage (human determination)
    TRIAGED --> ASSIGNED : assign
    ASSIGNED --> ASSIGNED : reassign
    ASSIGNED --> IN_REVIEW : begin_review
    ASSIGNED --> AWAITING_CITIZEN : request_information
    ASSIGNED --> ESCALATED : escalate

    AWAITING_CITIZEN --> IN_REVIEW : information_received
    AWAITING_CITIZEN --> ESCALATED : escalate
    IN_REVIEW --> AWAITING_CITIZEN : request_information
    IN_REVIEW --> ESCALATED : escalate
    ESCALATED --> IN_REVIEW : return_to_officer
    ESCALATED --> AWAITING_CITIZEN : request_information

    IN_REVIEW --> RESOLVED : resolve (human determination)
    ESCALATED --> RESOLVED : resolve (human determination)
    RESOLVED --> IN_REVIEW : reopen

    NEW --> CLOSED : close
    TRIAGED --> CLOSED : close
    AWAITING_CITIZEN --> CLOSED : close (lapsed)
    ESCALATED --> CLOSED : close
    RESOLVED --> CLOSED : close

    CLOSED --> [*]

    note right of AWAITING_CITIZEN : SLA clock paused.
    note right of CLOSED : Only terminal state. Never reopened.
```

---

## 4. Implementation notes for the track that builds this

- **One transition table per machine**, in `app/domain/<context>.py`, as an immutable mapping
  `dict[tuple[State, Event], Transition]` where `Transition` carries `to_state`, `permission`,
  `audit_action`, `requires_reason`, and a tuple of guard callables. The tables above are the source;
  the module is the transcription.
- **One generic executor** in `app/services/workflow.py`:
  `apply_event(session, obj, event, actor, reason=None, detail=None) -> Transition`. It performs, in
  order: version check → table lookup → permission check → guards → state write → `audit_events`
  insert → (consular only) `case_events` insert. Any failure raises before the state write.
- **Guards are pure predicates** over `(obj, session, actor)`. Examples above: "approver ≠ drafter",
  "linked stakeholder exists", "a reminder was previously sent". They return a reason string on
  failure so the API can explain the block rather than merely returning 409.
- **Non-autonomous controls (⚠)** additionally assert `actor.is_human` and that no Gateway call is on
  the current call stack. The four §6 controls landing in these tables are: `commit:opportunity`
  (commitments), `approve`/`send:meeting_followup` (diplomatic communication and external outreach),
  `triage`/`resolve:consular_case` (consular determinations), `close:consular_case` (case closure).
- **Tests required before this contract is considered implemented:**
  1. Every `(state, event)` pair in these tables produces exactly the listed target state.
  2. Every pair **not** in the tables raises `IllegalTransition` — asserted by iterating the full
     cartesian product of states × events per machine.
  3. Every legal transition writes exactly one `audit_events` row with the listed `action`.
  4. A rolled-back transition writes no audit row.
  5. Every terminal state rejects every event.
  6. `SENT` is unreachable except from `APPROVED`.
  7. `approve:meeting_followup` by the drafter is refused.
  8. Every permission in the grant tables exists in the RBAC matrix, and no machine references a
     permission the matrix does not define.
  9. Re-firing a satisfied event is idempotent and writes no second audit row.

## 5. References

- `BUILD_BIBLE.md` §6 (non-negotiable controls), §9 (state machines)
- `CLAUDE.md` §2.3 (audit on every transition), §5 (logic in services, not handlers)
- ADR-0003 (RBAC), ADR-0004 (append-only audit), ADR-0006 (classification), ADR-0007 (`public_ref`)
- `data/taxonomy/consular_case_types.json` (SLA budgets, determination flags)
- `docs/OPEN_QUESTIONS.md` Q-04, Q-05
