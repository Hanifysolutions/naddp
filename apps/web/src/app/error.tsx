'use client';

import * as React from 'react';
import { RefreshCw, TriangleAlert } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { DemoBadge } from '@/components/layout/demo-badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

/**
 * Route error boundary.
 *
 * The demo must never dead-end (BUILD_BIBLE §0), and "never dead-end" does not mean
 * "never fail" - it means a failure always leaves the operator somewhere they can act
 * from. So this screen: states plainly that something broke, shows the digest so the
 * failure can be traced in the server log, offers a retry that re-renders the segment,
 * and keeps a route back to the command centre.
 *
 * It deliberately does not paraphrase the error into reassuring prose. `error.message`
 * is shown as-is in development; in production Next replaces it with a generic string
 * and supplies `digest` instead, which is the value that actually correlates to a log
 * entry.
 */
export default function GlobalRouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}): React.JSX.Element {
  React.useEffect(() => {
    // Surface the failure in the browser console as well as on screen. A demo operator
    // watching devtools should not have to guess what happened.
    console.error('[naddp] route error boundary caught:', error);
  }, [error]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-6">
      <Card className="w-full max-w-xl">
        <CardHeader className="gap-2">
          <div className="flex items-center justify-between gap-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <TriangleAlert aria-hidden="true" className="h-5 w-5 text-destructive" />
              Something failed on this screen
            </CardTitle>
            <DemoBadge />
          </div>
        </CardHeader>

        <CardContent className="space-y-4">
          <p className="text-sm leading-relaxed text-muted-foreground">
            The page could not be rendered. Nothing was changed and no state transition
            was recorded. You can retry this section, or return to the command centre.
          </p>

          <div className="rounded-md border border-border bg-muted p-3">
            <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
              Reported error
            </p>
            <p className="mt-1 break-words font-mono text-xs text-foreground">
              {error.message.length > 0 ? error.message : 'No message was provided.'}
            </p>
            {error.digest === undefined ? null : (
              <p className="mt-2 font-mono text-2xs text-muted-foreground">
                digest {error.digest}
              </p>
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            <Button onClick={reset}>
              <RefreshCw aria-hidden="true" />
              Try again
            </Button>
            {/*
              A hard navigation, not a soft one. If the failure is in a shared layout or
              a provider, a client-side transition would re-enter the same broken tree;
              a full document load rebuilds it from scratch.
            */}
            <Button variant="outline" onClick={goToCommandCentre}>
              Back to command centre
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/** A deliberate full document load. See the call site for why it is not a soft nav. */
function goToCommandCentre(): void {
  window.location.assign('/command');
}
