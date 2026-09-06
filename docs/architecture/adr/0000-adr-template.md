# ADR-0000: <Short, decision-shaped title in the imperative or noun form>

- **Status:** Proposed | Accepted | Superseded by ADR-XXXX | Deprecated
- **Date:** YYYY-MM-DD
- **Deciders:** <roles, not just names>
- **Consulted:** <who was asked>
- **Informed:** <who needs to know>
- **Tags:** `<bounded-context>` `<security | data | platform | ai | ops>`

## Context

What forces are in play? State the problem, the constraints that are genuinely fixed (regulatory,
schedule, existing decisions in other ADRs), and the ones that only look fixed. Name the specific
NADDP requirement this serves — cite `BUILD_BIBLE.md` sections or `CLAUDE.md` rules by number.

Keep this factual. If a constraint is an assumption rather than a fact, say so and link the
corresponding row in `docs/OPEN_QUESTIONS.md`.

## Decision

One paragraph, present tense, unambiguous: "We will …". Then the specifics — interfaces, module
boundaries, enum values, table names — precise enough that a reader can tell whether a given pull
request complies.

## Consequences

### Positive
What this buys us, tied back to the forces in Context.

### Negative / costs
What it costs. Be honest; an ADR with no negative consequences has not been thought about.

### Neutral / follow-on work
What now has to happen elsewhere: migrations, lint rules, tests, documentation, other ADRs.

## Alternatives considered

### <Alternative A>
What it is, why it was plausible, and the specific reason it lost.

### <Alternative B>
Same.

### Do nothing
Always consider it explicitly. What happens if we defer this decision?

## Enforcement

How a violation of this decision is *detected*, not merely discouraged. Prefer, in order:
a type/compiler constraint, a database constraint, an automated test, a lint rule, a CI gate,
a code-review checklist item. State which of these exist today and which are deferred.

## References

- `BUILD_BIBLE.md` §N
- `CLAUDE.md` §N
- Related ADRs
