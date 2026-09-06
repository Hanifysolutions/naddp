# Grounding Evals

**The claim being tested:** every substantive statement NADDP makes is traceable to a specific piece
of evidence the requesting user is authorised to read, and the citation resolves.

This is winning moment #1 — *"not a chatbot"* (`BUILD_BIBLE.md` §1). A brief that is accurate but
uncited fails, because the product's argument is not "the model is right", it is
"you can check, and here is where to look".

Cases: [`cases.example.jsonl`](cases.example.jsonl) · Format: [`../README.md`](../README.md)

---

## What a grounding failure looks like

Four distinct defects, each of which a case must be able to distinguish:

| Failure | Description | Caught by |
|---|---|---|
| **Uncited claim** | A specific, checkable assertion with no evidence reference. | `must_cite: true` |
| **Fabricated citation** | An evidence ID that does not exist, or a URL not present in the citation registry. | Gateway stage 8; the case asserts the response was not served |
| **Unsupported citation** | A real, resolvable citation attached to a claim the cited source does not support. Attribution laundering — the most dangerous of the four, because it survives every automated check except reading the source. | Human review, sampled; `expected_evidence_ids` narrows the search |
| **Silent omission** | The evidence exists, is authorised, and is materially relevant, but the answer does not surface it. The hero-thread diaspora search returning only the lithium engineer and not the migration academic is exactly this. | `must_include` |

The fourth is why grounding is not simply "did it cite something". A brief that cites one of three
relevant signals is well-cited and misleading.

## Refusal is a grounded behaviour

`must_refuse: true` appears in this suite, not only in the security suite. `knowledge_answer` must
refuse when no approved source supports an answer (`ROADMAP.md`, Week 3). A confident, uncited,
plausible answer to a question the corpus cannot support is the single worst output this system could
produce in front of a consular audience — worse than an error, because it is not visibly wrong.

A correct refusal:

- states that no approved source covers the question,
- does not speculate, hedge into an answer, or offer "generally speaking…",
- offers the escalation path (which officer or which knowledge gap to raise),
- still writes a trace.

## Hero-thread coverage

The seed is built around one narrative (`BUILD_BIBLE.md` §2), so the grounding suite follows it. The
committed cases cover:

| Case | Purpose | Hero-thread beat |
|---|---|---|
| `gnd-001` | `morning_brief` | The lithium refinery signal reaches the Ambassador's brief, cited. |
| `gnd-002` | `opportunity_score` | The scoring of the lithium + skilled-migration opportunity is explainable and evidence-backed. |
| `gnd-003` | `diaspora_match` | Search surfaces **both** the processing engineer and the migration academic — the silent-omission case. |
| `gnd-004` | `knowledge_answer` | A question the approved corpus does not cover is refused, not answered. |
| `gnd-005` | `meeting_prep` | The pre-read is assembled from the stakeholder's own interaction history, cited, and does not import claims from elsewhere. |

## Assertions the runner performs

For every case, in this order — a failure at any step stops evaluation of that case and is reported
with the step name:

1. **Envelope.** The response is `{ result, evidence, trace_id, approval_status }` and `result`
   validates against the purpose's schema.
2. **Trace.** An `ai_traces` row exists for `trace_id`, and its `fallback` flag matches the run mode.
3. **Refusal.** If `must_refuse`, the response is a refusal and carries no substantive claims. If not
   `must_refuse`, the response is not a refusal.
4. **Citation coverage.** If `must_cite`, every claim-bearing field carries at least one evidence
   reference.
5. **Citation resolution.** Every referenced evidence ID exists, is readable by `actor.role`, and —
   for intelligence evidence — maps to an entry in `data/demo-seed/citations.json` that is not flagged
   `TODO_VERIFY`.
6. **Expected evidence.** Every slug in `expected_evidence_ids` is cited.
7. **Forbidden evidence.** No slug in `forbidden_evidence_ids` is cited.
8. **Inclusion / exclusion.** `must_include` and `must_not_include` hold against the serialised
   `result`.
9. **Approval status.** Matches `expected_approval_status` when specified.

Step 5's `TODO_VERIFY` check is deliberate: an unverified URL is not grounding, it is a promise. All
must be cleared before the Week 4 rehearsal (`CLAUDE.md` §2.6).

## Authoring guidance

- **Assert on evidence, not on wording.** `must_include` should hold entity names and identifiers, not
  sentences. Asserting on prose produces a suite that breaks on every prompt edit and teaches the team
  to disable cases.
- **One defect per case.** A case that asserts eight things reports one failure and hides seven.
- **Pin the role.** Grounding is role-relative: the same question answered for an `AMBASSADOR` and a
  `TRADE_OFFICER` should cite different evidence sets, because they may read different things.
- **Every case must be able to fail.** If you cannot describe the broken system that would fail it,
  it is not testing anything.
