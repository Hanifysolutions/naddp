'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { LoaderCircle, TriangleAlert } from 'lucide-react';
import {
  isApiError,
  NADDP_ROLES,
  toApiError,
  type ApiError,
  type NaddpRole,
} from '@naddp/contracts';

import {
  Select,
  SelectContent,
  SelectItem,
  SelectLabel,
  SelectGroup,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { assumeRole as postAssumeRole } from '@/lib/api-queries';
import { cn } from '@/lib/utils';

/**
 * Presentation labels for the six demo identities. These are captions only - the role
 * identifiers sent to the API are the enum values from `@naddp/contracts`, and the API
 * decides what each role may do. Nothing here grants anything.
 */
const ROLE_LABELS: Readonly<Record<NaddpRole, string>> = {
  AMBASSADOR: 'Ambassador',
  DEPUTY: 'Deputy Head of Mission',
  TRADE_OFFICER: 'Trade Officer',
  CONSULAR_OFFICER: 'Consular Officer',
  DIASPORA_OFFICER: 'Diaspora Officer',
  ADMIN: 'Administrator',
};

export interface RolePickerProps {
  /**
   * The role the API says this session currently holds, or `null` when no session has
   * been established. Resolved server-side in `app/command/layout.tsx`.
   */
  activeRole?: NaddpRole | null;
  className?: string;
}

/**
 * Demo identity switcher.
 *
 * Only the *identity* is faked in this product. RBAC is real and deny-by-default from day
 * one (CLAUDE.md §2.4), so this control does exactly one thing: ask the API to issue a
 * signed `naddp_demo_session` cookie for the chosen role. Every authorisation decision that
 * follows is made server-side against that cookie.
 *
 * The success path has three steps and skipping any of them would leave the screen lying:
 *
 *   1. **Adopt the role the API returned**, not the one that was requested. They agree in
 *      every expected case; trusting the response is what makes an unexpected one visible.
 *   2. **Drop every cached read.** `removeQueries()` rather than `invalidateQueries()`:
 *      each cache entry was authorised for the previous identity, and removal means no
 *      component can render one for a frame while the refetch is in flight. Query keys
 *      carry the role too, so this is the second of two independent guards.
 *   3. **`router.refresh()`**, which re-runs the server layout with the new cookie and
 *      rebuilds the navigation rail from the new permission set.
 *
 * Failure behaviour is deliberate and load-bearing. When the call fails for any reason -
 * API down, CORS, RBAC refusal - this component reverts the visible selection to the role
 * the API last confirmed, renders the status code and the API's own message inline, and
 * never optimistically pretends the switch happened. A demo that silently shows the wrong
 * identity is far more dangerous in front of a consular audience than one that says plainly
 * that a call failed.
 */
export function RolePicker({
  activeRole = null,
  className,
}: RolePickerProps): React.JSX.Element {
  const router = useRouter();
  const queryClient = useQueryClient();

  // `confirmed` is what the API has acknowledged; `selected` is what the trigger shows.
  // They diverge only while a request is in flight, and re-converge on success or on
  // failure - never on a guess.
  const [confirmed, setConfirmed] = React.useState<NaddpRole | null>(activeRole);
  const [selected, setSelected] = React.useState<NaddpRole | null>(activeRole);
  const [pending, setPending] = React.useState(false);
  const [error, setError] = React.useState<ApiError | null>(null);

  // Keep in step if the server component re-renders with a newly resolved session.
  React.useEffect(() => {
    setConfirmed(activeRole);
    setSelected(activeRole);
  }, [activeRole]);

  const abortRef = React.useRef<AbortController | null>(null);
  React.useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const assume = React.useCallback(
    async (nextRole: NaddpRole): Promise<void> => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setSelected(nextRole);
      setPending(true);
      setError(null);

      try {
        const session = await postAssumeRole(nextRole, controller.signal);

        // Step 1: the API's answer, not the request.
        setConfirmed(session.role);
        setSelected(session.role);

        // Step 2: every cached read was authorised for the previous identity.
        queryClient.removeQueries();

        // Step 3: rebuild the server-rendered chrome - rail, identity, permissions.
        router.refresh();
      } catch (failure) {
        if (failure instanceof DOMException && failure.name === 'AbortError') {
          // Superseded by a newer selection, or the component unmounted. Not a failure.
          return;
        }
        // Revert to the last identity the API actually confirmed.
        setSelected(confirmed);
        setError(normaliseFailure(failure));
      } finally {
        if (!controller.signal.aborted) {
          setPending(false);
        }
      }
    },
    [confirmed, queryClient, router],
  );

  const handleChange = React.useCallback(
    (value: string): void => {
      if (!isNaddpRole(value)) {
        setError(toApiError(0, { detail: `"${value}" is not a role this API defines.` }));
        return;
      }
      void assume(value);
    },
    [assume],
  );

  const labelId = 'role-picker-label';
  const errorId = 'role-picker-error';

  return (
    <div className={cn('relative', className)}>
      <div className="flex items-center gap-2">
        <span id={labelId} className="sr-only">
          Demo identity: choose the role to view the mission as
        </span>

        <Select value={selected ?? ''} onValueChange={handleChange} disabled={pending}>
          <SelectTrigger
            aria-labelledby={labelId}
            aria-describedby={error === null ? undefined : errorId}
            aria-invalid={error !== null}
            className={cn(
              /*
               * The picker sits ON the --slate-900 command bar, so it takes the dark
               * surface rather than the card fill it used to have.
               *
               * --slate-400 as the border is a NON-TEXT use of the token globals.css
               * restricts: as the control's boundary it measures 4.09:1 against the bar,
               * clearing the 3:1 SC 1.4.11 asks of a component edge, where --slate-700
               * would have managed only 1.49:1. The label text is white (14.29:1) and
               * the placeholder --slate-300 (4.61:1).
               */
              'h-8 w-[13.5rem] border-slate-400 bg-slate-900 text-sm text-white shadow-none',
              'hover:bg-ink data-[placeholder]:text-slate-300',
              'focus:ring-accent-on-dark focus:ring-offset-slate-900',
              /*
               * The failed state adds --risk, which is only 2.43:1 on this bar - enough
               * to be seen as a change, not enough to BE the boundary. The light hairline
               * therefore stays underneath it as a ring, and the failure itself is
               * carried by the alert below in words and an icon, never by colour.
               */
              error !== null && 'border-risk ring-1 ring-slate-400',
            )}
          >
            <SelectValue placeholder="Choose a demo role" />
          </SelectTrigger>

          <SelectContent align="end">
            <SelectGroup>
              <SelectLabel>Demo identity</SelectLabel>
              {NADDP_ROLES.map((role) => (
                <SelectItem key={role} value={role}>
                  {ROLE_LABELS[role]}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>

        {pending ? (
          <span
            className="flex items-center gap-1.5 text-xs text-slate-300"
            role="status"
          >
            <LoaderCircle aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />
            Switching
          </span>
        ) : null}
      </div>

      {error === null ? null : (
        /*
          The panel hangs off the bar and over the --paper work area, and it is held
          there by a --risk hairline at 5.55:1 rather than by a drop shadow: hierarchy in
          this system comes from colour and rule, not from card chrome.
        */
        <div
          id={errorId}
          role="alert"
          className="absolute right-0 top-[calc(100%+0.5rem)] z-50 w-[22rem] rounded-md border border-risk/60 bg-popover p-3 text-popover-foreground"
        >
          <p className="flex items-start gap-2 text-sm font-medium">
            <TriangleAlert
              aria-hidden="true"
              className="mt-0.5 h-4 w-4 shrink-0 text-risk"
            />
            <span>
              Could not switch role
              {error.status > 0 ? ` (HTTP ${error.status})` : ''}
            </span>
          </p>
          <p className="mt-1.5 pl-6 text-sm leading-snug text-slate-700">
            {error.message}
          </p>
          {/*
            Diagnostics as labelled fields, not as `missing X` / `request Y` runs of
            monospace. The LABEL is prose and stays in the body face; only the VALUE is an
            identifier, and identifiers are the one thing monospace still exists for here.
          */}
          {error.missingPermissions.length === 0 &&
          error.requestId === null &&
          error.traceId === null ? null : (
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 pl-6 text-2xs">
              {error.missingPermissions.length === 0 ? null : (
                <>
                  <dt className="text-slate-700">Missing permissions</dt>
                  <dd className="break-words font-mono">
                    {error.missingPermissions.join(', ')}
                  </dd>
                </>
              )}
              {error.requestId === null ? null : (
                <>
                  <dt className="text-slate-700">Request</dt>
                  <dd className="break-all font-mono">{error.requestId}</dd>
                </>
              )}
              {error.traceId === null ? null : (
                <>
                  <dt className="text-slate-700">Trace</dt>
                  <dd className="break-all font-mono">{error.traceId}</dd>
                </>
              )}
            </dl>
          )}
          <p className="mt-2 pl-6 text-2xs leading-snug text-slate-700">
            The identity shown above is unchanged. Nothing was assumed.
          </p>
        </div>
      )}
    </div>
  );
}

/** Runtime guard so an unexpected select value can never widen into an unchecked role. */
function isNaddpRole(value: string): value is NaddpRole {
  return (NADDP_ROLES as readonly string[]).includes(value);
}

/** Last-resort normalisation for a thrown value that is not already an ApiError. */
function normaliseFailure(failure: unknown): ApiError {
  if (isApiError(failure)) return failure;
  return toApiError(0, {
    detail: failure instanceof Error ? failure.message : 'An unexpected error occurred.',
  });
}
