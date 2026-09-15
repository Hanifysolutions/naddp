'use client';

import * as React from 'react';
import { useMutation } from '@tanstack/react-query';
import { AlertTriangle, LoaderCircle, ShieldAlert, ShieldCheck } from 'lucide-react';
import { isApiError, type AuditChainVerification } from '@naddp/contracts';

import { PRIMARY_ACTION } from '@/components/meetings/action-styles';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { verifyAuditChain } from '@/lib/api-queries';

/**
 * Verify the audit chain: the trust moment, on demand.
 *
 * One action calls `GET /v1/audit/chain`, which recomputes every entry's digest and every link
 * from the first entry to the latest, and this panel says what came back - in words a
 * non-technical reader can repeat. An intact chain is a calm confirmation on the --ok tick; a break
 * is named, with the entry and the reason, on the --risk tick. Nothing is inferred here: the count,
 * the verdict and the position are the API's.
 *
 * The chain can be trusted to mean what it says under load: the database refuses a second entry
 * claiming the same predecessor, so concurrent writes cannot fork it and a break is always a real
 * finding (closes the Week 1 fork limitation).
 */

const NUMBER_FORMAT = new Intl.NumberFormat('en-AU');
const TIME_FORMAT = new Intl.DateTimeFormat('en-AU', {
  dateStyle: 'medium',
  timeStyle: 'medium',
});

export function ChainVerificationPanel(): React.JSX.Element {
  const headingId = React.useId();
  const verify = useMutation({ mutationFn: () => verifyAuditChain() });

  return (
    <section
      aria-labelledby={headingId}
      className="overflow-hidden rounded-lg border border-line bg-card"
    >
      <div className="flex flex-wrap items-start justify-between gap-4 p-4">
        <div className="min-w-0 max-w-[72ch] space-y-1">
          <h2
            id={headingId}
            className="flex items-center gap-2 text-base font-semibold leading-tight text-ink"
          >
            <ShieldCheck aria-hidden="true" className="h-4 w-4 text-accent" />
            Tamper evidence
          </h2>
          <p className="text-sm leading-relaxed text-slate-700">
            Every entry carries a SHA-256 digest of its own contents and of the entry
            before it, so altering or removing any entry breaks every link after it.
            Verification recomputes each digest and each link across the whole log -
            including entries your clearance does not show you - and discloses only
            whether the chain holds.
          </p>
        </div>
        <Button
          type="button"
          onClick={() => verify.mutate()}
          disabled={verify.isPending}
          className={PRIMARY_ACTION}
        >
          {verify.isPending ? (
            <>
              <LoaderCircle aria-hidden="true" className="h-4 w-4 animate-spin" />
              Verifying…
            </>
          ) : (
            <>
              <ShieldCheck aria-hidden="true" className="h-4 w-4" />
              Verify audit chain
            </>
          )}
        </Button>
      </div>

      <div aria-live="polite">
        {verify.isError ? (
          <div className="border-t border-line p-4">
            <Alert variant="destructive" role="status">
              <AlertTriangle aria-hidden="true" />
              <AlertTitle className="leading-snug">
                The verification could not be run
              </AlertTitle>
              <AlertDescription className="max-w-[72ch]">
                {isApiError(verify.error)
                  ? verify.error.message
                  : 'The API did not answer.'}{' '}
                Nothing was verified, so this is not a result either way.
              </AlertDescription>
            </Alert>
          </div>
        ) : null}
        {verify.isSuccess ? (
          <VerificationResult result={verify.data} verifiedAt={verify.submittedAt} />
        ) : null}
      </div>
    </section>
  );
}

function VerificationResult({
  result,
  verifiedAt,
}: {
  result: AuditChainVerification;
  verifiedAt: number;
}): React.JSX.Element {
  const when = TIME_FORMAT.format(new Date(verifiedAt));
  const scope =
    result.limit === null || result.limit === undefined
      ? 'The whole log was checked, from its first entry to its latest.'
      : `The most recent ${NUMBER_FORMAT.format(result.limit)} entries were checked.`;

  if (result.is_intact) {
    return (
      <div className="tick-ok border-t border-line px-4 py-4" role="status">
        <p className="tabular flex items-center gap-2 text-lg font-semibold leading-snug text-ok">
          <ShieldCheck aria-hidden="true" className="h-5 w-5 shrink-0" />
          Chain intact over {NUMBER_FORMAT.format(result.checked)} events.
        </p>
        <p className="mt-1.5 max-w-[72ch] text-sm leading-relaxed text-ink">
          Each entry is cryptographically linked to the one before it; no entry has been
          altered or removed.
        </p>
        <p className="mt-2 max-w-[80ch] text-label leading-snug text-slate-700">
          Verified {when}. {scope} Concurrent writes cannot fork the chain: the database
          refuses a second entry that claims the same predecessor, so a verification can
          only report a real break.
        </p>
        <p className="mt-1.5 max-w-[80ch] text-2xs leading-snug text-slate-700">
          What this proves: every stored digest still matches its entry and the entry
          before it. Copying the latest digest off the platform, which would also detect a
          wholesale rewrite of the log, is pilot work (ADR-0004).
        </p>
      </div>
    );
  }

  return (
    <div className="tick-risk border-t border-line px-4 py-4" role="alert">
      <p className="flex items-center gap-2 text-lg font-semibold leading-snug text-risk">
        <ShieldAlert aria-hidden="true" className="h-5 w-5 shrink-0" />
        Chain broken.
      </p>
      <p className="tabular mt-1.5 max-w-[72ch] text-sm leading-relaxed text-ink">
        {NUMBER_FORMAT.format(result.checked)} events verified before the break, which is
        at position {NUMBER_FORMAT.format(result.broken_at_index ?? result.checked)},
        entry{' '}
        <span className="font-mono text-xs">{result.broken_at_id ?? 'unknown'}</span>.
      </p>
      {result.reason === null || result.reason === undefined ? null : (
        <p className="mt-1.5 max-w-[80ch] text-label leading-snug text-slate-700">
          {result.reason}
        </p>
      )}
      <p className="mt-2 max-w-[80ch] text-label leading-snug text-slate-700">
        Verified {when}. A break is a finding: concurrent writes cannot produce one. Read
        the log from this entry.
      </p>
    </div>
  );
}
