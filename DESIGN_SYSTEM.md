# NADDP — Design System (v1.0)
**Binding visual contract. The Intelligence page and every Week 3+ surface is built to this. Week 4 applies it across all existing screens.**

Audience: a Head of Mission / Ambassador — a senior diplomat, not a SaaS user. The screen is an *instrument the mission trusts*, closer to an operations centre or a financial terminal than a startup dashboard. Credibility in the first ten seconds is load-bearing.

## Design intent (the one-liner)
A calm, authoritative mission instrument. Quiet by default so that the *data* and the *governance* are the loudest things on screen. Spend boldness in exactly one place per screen — the primary figure or the hero brief item — and keep everything else disciplined.

## Anti-goals (do NOT do these — they read as generated)
- No ALL-CAPS eyebrow labels above headings (the current "COUNTED ACROSS" / "PIPELINE BY STAGE" treatment goes).
- No `A · B · C` middle-dot meta strings as a structural device.
- No `→` appended to links/buttons.
- No monospace for data labels (numbers themselves may use tabular figures — different thing).
- No single-word colour/italic accent inside a headline.
- Not "near-black + one bright acid accent". Not warm-cream + terracotta (that's the Claude tell).
- No uniform rounded-card kit with identical radius + soft grey shadow on everything.

## Colour — "Mission Slate"
A restrained institutional palette. Deep slate-navy as the authority colour, warm neutral paper, one disciplined accent used ONLY for the live/actionable, and semantic colours reserved strictly for state.

| Token | Hex | Use |
|---|---|---|
| `--ink` | `#141B2E` | Primary text, darkest structure (a true dark navy, not tinted black) |
| `--slate-900` | `#1E2A44` | Command surfaces, top bar, primary headings |
| `--slate-700` | `#38455F` | Secondary text, borders on dark |
| `--slate-400` | `#7C8AA5` | Muted labels, meta (replaces the caps eyebrows) |
| `--paper` | `#F7F8FA` | App background (cool paper, not cream) |
| `--surface` | `#FFFFFF` | Card/panel surface |
| `--line` | `#E3E7EE` | Hairlines, dividers, card borders (1px, low-contrast) |
| `--accent` | `#1D6A73` | THE accent — deep teal. Live counts, active nav, primary action. Used sparingly. |
| `--accent-weak`| `#E6F0F0` | Accent tint for selected/active backgrounds |
| Semantic | | reserved for STATE only, never decoration |
| `--ok` | `#2E7D5B` | Allowed / healthy / resolved |
| `--warn` | `#B4791F` | SLA-risk / overdue / awaiting |
| `--risk` | `#B23A3A` | Breached / denied / critical |
| `--proposed`| `#6B5CA5` | AI-proposed / unconfirmed (the Q-17 honesty colour — muted violet, visually SUBORDINATE to evidenced items) |

The AI-proposed violet must read as *quieter/cooler* than evidenced items — the honesty gap is shown chromatically, not just numerically.

## Typography
Two families, clearly distinct:
- **Display / headings / big figures:** a serious grotesque with tabular figures — **"Söhne", "Inter Tight", or "Geist"** (pick one available; Inter Tight is a safe pnpm-installable default). Weight 600 for headings, tabular-nums for all data figures.
- **Body / labels:** **Inter** (or system UI stack) at 400/500.
- Type scale (major third, 1.25): 12.8 / 16 / 20 / 25 / 31 / 39 / 49px. Data figures on tiles use the 31–39 step.
- Sentence case everywhere. No tracked-out caps. Labels are quiet slate-400, small, sentence case.
- Line length ≤ 72ch for any prose (brief item bodies).

## Layout & structure
- **Command surfaces** (top bar, left rail) sit on `--slate-900` — dark, authoritative, framing. The work area is `--paper`.
- **Tiles are not identical rounded cards.** Use hairline `--line` borders, small radius (6px), NO drop shadows — flat, instrument-panel, separated by rules and space not shadow. Hierarchy comes from the figure size and one accent, not from card chrome.
- **The primary figure per tile** is the loud element: large tabular figure in `--ink`, its label quiet beneath. Live/actionable figures take `--accent`.
- Structural devices encode meaning: a left border-tick in a semantic colour marks state (risk/warn/proposed) — that's information, not decoration. Don't number things that aren't sequences.
- Generous whitespace; calm density. A diplomat scanning, not an analyst hunting.

## Motion
One orchestrated moment only: the command centre figures count-up once on load (respect `prefers-reduced-motion`). No per-card hover-lift, no section fade-ins. Motion otherwise only confirms an action (a transition firing, an approval landing).

## Keep these strengths from the current build (do NOT lose in redesign)
- The refusal/deny copy ("this is a refusal, not a count of zero").
- "Cleared to read" / clearance-scoped framing.
- The classification-badge honesty.
- The AI-proposed labelling.
These are what win a security-minded room. Restyle them; never remove them.

## Quality floor (non-negotiable)
Responsive to mobile; visible keyboard focus; `prefers-reduced-motion` respected; WCAG 2.2 AA contrast (check the teal accent on white and the semantic colours); tabular figures for all numbers so columns align.
