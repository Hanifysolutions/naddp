'use client';

import * as React from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { isApiError, type CaseTransitionRequest, type NaddpRole } from '@naddp/contracts';

import { proposeCaseTriage, transitionCase } from '@/lib/api-queries';
import { queryKeys } from '@/lib/query-keys';

/**
 * The two consular writes a case workspace makes, with one refusal channel.
 *
 * **`transition`** is a named officer firing one event. Every settled transition refetches the
 * case, the dashboard and the command centre: on success the state has moved, and on a refusal
 * it may have moved under us (a 409 precondition means somebody else acted first). Nothing is
 * patched locally, because `available_events` and the timeline are the server's to decide.
 *
 * **`triage`** asks the AI Gateway for a recommendation. It writes a trace and changes nothing
 * about the case, so it refetches nothing; its envelope lives in the mutation state for as long
 * as the workspace is open, and the officer's decision is a separate `transition`.
 *
 * **A refusal is kept in the server's own words**, the idiom of `use-followup-actions`: a 403 or
 * 409 from the transition route is written to the audit log as a DENY row before the API
 * answers, so the UI may say so; a network failure was not, and the sentence is withheld.
 */

export interface Refusal {
  readonly title: string;
  readonly message: string;
  /** True only for answers the server records as DENY rows: 403 and 409. */
  readonly audited: boolean;
}

function describeRefusal(error: unknown): Refusal {
  if (!isApiError(error)) {
    return {
      title: 'The action could not be completed',
      message: error instanceof Error ? error.message : 'The API did not answer.',
      audited: false,
    };
  }
  const message = [
    error.message,
    error.missingPermissions.length > 0
      ? `Missing: ${error.missingPermissions.join(', ')}.`
      : null,
  ]
    .filter((part): part is string => part !== null)
    .join(' ');
  if (error.status === 403) return { title: 'Refused by the server', message, audited: true };
  if (error.status === 409) return { title: 'Not applied', message, audited: true };
  return { title: 'The action could not be completed', message, audited: false };
}

export function useCaseActions(role: NaddpRole | null, caseId: string) {
  const queryClient = useQueryClient();
  const [refusal, setRefusal] = React.useState<Refusal | null>(null);

  const refetch = React.useCallback(async (): Promise<void> => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.consularCase(role, caseId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.consularDashboard(role) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.commandToday(role) }),
    ]);
  }, [queryClient, role, caseId]);

  const onError = React.useCallback((error: unknown) => {
    setRefusal(describeRefusal(error));
  }, []);

  const transition = useMutation({
    mutationFn: (body: CaseTransitionRequest) => transitionCase(caseId, body),
    onMutate: () => setRefusal(null),
    onError,
    onSettled: () => refetch(),
  });

  const triage = useMutation({
    mutationFn: () => proposeCaseTriage(caseId),
    onMutate: () => setRefusal(null),
    onError,
  });

  const dismissRefusal = React.useCallback(() => setRefusal(null), []);

  return { transition, triage, refusal, dismissRefusal };
}

export type CaseActions = ReturnType<typeof useCaseActions>;
