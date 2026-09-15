'use client';

import * as React from 'react';
import { AlertTriangle, Lock } from 'lucide-react';

import type { Refusal } from '@/components/meetings/use-followup-actions';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';

/**
 * A refused follow-up action, in the server's own words.
 *
 * The pipeline board's refusal idiom: a warning tone for a control working (403) or a state
 * that moved on (409), because neither is a fault, and a destructive tone only when the API
 * did not answer at all. The audit sentence is printed only when the refusal is one the
 * server records - claiming an audit row that was never written would be the UI inventing
 * governance it cannot see.
 */
export function RefusalAlert({ refusal }: { refusal: Refusal }): React.JSX.Element {
  return (
    <Alert variant={refusal.audited ? 'warning' : 'destructive'} role="status">
      {refusal.audited ? <Lock aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
      <AlertTitle className={refusal.audited ? 'leading-snug text-warn-ink' : 'leading-snug'}>
        {refusal.title}
      </AlertTitle>
      <AlertDescription className="max-w-[72ch]">
        {refusal.message}
        {refusal.audited ? ' The attempt was written to the audit log.' : null}
      </AlertDescription>
    </Alert>
  );
}
