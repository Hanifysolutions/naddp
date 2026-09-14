'use client';

import * as React from 'react';
import { Route } from 'lucide-react';
import type { BriefTrace } from '@naddp/contracts';

import { TraceDrawer } from '@/components/intelligence/trace-drawer';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Sheet, SheetTrigger } from '@/components/ui/sheet';

/**
 * The routing badge, and the way into the trace drawer.
 *
 * **Absence here is a decision, not a gap.** `trace` is null when the API declined to embed
 * the routing decision - the caller lacks `read:ai_trace`, or does not clear the dominant of
 * the declared and answered zones. That judgement was made server-side and re-applied by
 * `app.services.briefs.trace_for_brief` precisely so that embedding the badge in the brief is
 * not a side-channel around `GET /v1/ai/traces/{trace_id}`. Rendering nothing is therefore
 * the correct and complete behaviour; the brief header says the decision was withheld, which
 * is a different and more honest statement than a badge that fails to open.
 *
 * **The badge string is opaque.** It is rendered exactly as the Gateway wrote it at stage 5
 * and is never split, re-cased or rebuilt: there are five badge shapes with three or four
 * segments and the first segment is not always a classification name. An empty string means
 * the row predates the column, and it says so in words. Its own middle dots are that value's
 * formatting and stay verbatim, and it is set in mono because it is an identifier the API
 * produced - the one place DESIGN_SYSTEM.md keeps a monospace face. The chip around it is
 * deliberately quiet: an outline and slate text, so the routing decision is available to
 * anyone who wants it without competing with the brief for attention.
 *
 * **Fallback is its own chip.** CLAUDE.md rule 2.5 requires every AI call to have a
 * deterministic cached fallback, and `fallback=true` is the state the demo's hero brief is
 * genuinely in. Folding that into the badge text would put a claim inside a string this
 * component has just promised not to touch, and would let a reader mistake it for something
 * the Gateway said. It sits beside the badge, in the warning tone the product reserves for
 * "this is not the whole story" - a warn border with `warn-ink` text, because `--warn` is an
 * AA-safe border colour and not an AA-safe text colour (see `globals.css`).
 */
export function TraceBadge({
  trace,
}: {
  trace: BriefTrace | null;
}): React.JSX.Element | null {
  if (trace === null) return null;

  const badge = trace.route_badge === '' ? 'routing not recorded' : trace.route_badge;

  return (
    <span className="inline-flex items-center gap-1.5">
      <Sheet>
        <SheetTrigger asChild>
          {/* The Button is the control; the Badge inside it is the badge's own styling. The
              sr-only clause completes the accessible name, which would otherwise be the raw
              route string - readable, but not obviously a thing to press. */}
          <Button variant="ghost" size="sm" className="h-auto px-1.5 py-1">
            <Badge variant="outline" className="gap-1 font-mono text-2xs">
              <Route aria-hidden="true" className="h-3 w-3 text-slate-700" />
              {badge}
            </Badge>
            <span className="sr-only">Show how this brief was routed</span>
          </Button>
        </SheetTrigger>
        <TraceDrawer trace={trace} />
      </Sheet>

      {trace.fallback ? (
        <Badge
          variant="outline"
          className="border-warn bg-warn/5 text-2xs font-medium text-warn-ink"
        >
          fallback
          <span className="sr-only">
            : a stored snapshot answered instead of a live model call.
          </span>
        </Badge>
      ) : null}
    </span>
  );
}
