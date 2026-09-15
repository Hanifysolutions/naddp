'use client';

import * as React from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { isApiError, type Followup, type NaddpRole } from '@naddp/contracts';

import {
  approveFollowup,
  dispatchFollowup,
  draftFollowup,
  transitionFollowup,
} from '@/lib/api-queries';
import { queryKeys } from '@/lib/query-keys';

/**
 * The four follow-up writes, with one refusal channel and one invalidation rule.
 *
 * Shared by the meeting's follow-up panel and the approval queue, because both screens can
 * fire the same acts on the same rows and must not disagree about what happens afterwards.
 *
 * **Every settled mutation refetches four reads**: this meeting, the diary, the approval
 * queue and the command centre. On success the state has moved; on a refusal it may have
 * moved under us (a 409 precondition means somebody else acted first). A local patch would
 * guess at the server's new state - including `available_actions`, which the server alone
 * decides - so nothing is patched and everything touched is re-read.
 *
 * **A refusal is kept in the server's own words.** The message is the API's `detail`, plus
 * the permission it named when it named one. A 403 or 409 from these routes is written to
 * the audit log by the state machine before it answers (a DENY row), so the UI may say so; a
 * network failure or an unexpected status was not, and the sentence is withheld rather than
 * claimed.
 */

export interface Refusal {
  readonly title: string;
  readonly message: string;
  /** True only for answers the server records as DENY rows: 403 and 409. */
  readonly audited: boolean;
}

export type ReasonEvent = 'discard' | 'request_changes' | 'revoke_approval';

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
  if (error.status === 403) {
    return { title: 'Refused by the server', message, audited: true };
  }
  if (error.status === 409) {
    return { title: 'Not applied', message, audited: true };
  }
  return { title: 'The action could not be completed', message, audited: false };
}

export function useFollowupActions(role: NaddpRole | null) {
  const queryClient = useQueryClient();
  const [refusal, setRefusal] = React.useState<Refusal | null>(null);

  const refetchAfter = React.useCallback(
    async (meetingId: string): Promise<void> => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.meeting(role, meetingId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.meetings(role) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.approvalQueue(role) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.commandToday(role) }),
      ]);
    },
    [queryClient, role],
  );

  const onError = React.useCallback((error: unknown) => {
    setRefusal(describeRefusal(error));
  }, []);

  const dispatch = useMutation({
    mutationFn: (input: { meetingId: string; followup: Followup }) =>
      dispatchFollowup(input.meetingId, input.followup.id, input.followup.status),
    onMutate: () => setRefusal(null),
    onError,
    onSettled: (_data, _error, input) => refetchAfter(input.meetingId),
  });

  const approve = useMutation({
    mutationFn: (input: { meetingId: string; followup: Followup }) =>
      approveFollowup(input.meetingId, input.followup.id, input.followup.status),
    onMutate: () => setRefusal(null),
    onError,
    onSettled: (_data, _error, input) => refetchAfter(input.meetingId),
  });

  const transition = useMutation({
    mutationFn: (input: {
      meetingId: string;
      followup: Followup;
      event: ReasonEvent;
      reason: string;
    }) =>
      transitionFollowup(
        input.meetingId,
        input.followup.id,
        input.event,
        input.reason,
        input.followup.status,
      ),
    onMutate: () => setRefusal(null),
    onError,
    onSettled: (_data, _error, input) => refetchAfter(input.meetingId),
  });

  const draft = useMutation({
    mutationFn: (input: { meetingId: string }) => draftFollowup(input.meetingId),
    onMutate: () => setRefusal(null),
    onError,
    onSettled: (_data, _error, input) => refetchAfter(input.meetingId),
  });

  const dismissRefusal = React.useCallback(() => setRefusal(null), []);

  return { dispatch, approve, transition, draft, refusal, dismissRefusal };
}
