'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { LoaderCircle, TriangleAlert } from 'lucide-react';
import { NADDP_ROLES, toApiError, type ApiError, type NaddpRole } from '@naddp/contracts';

import {
  Select,
  SelectContent,
  SelectItem,
  SelectLabel,
  SelectGroup,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { API_BASE_URL } from '@/lib/api';
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
   * been established. Supplied by the caller once `GET /v1/session/me` exists; until
   * then the picker starts empty, which is the honest state.
   */
  activeRole?: NaddpRole | null;
  className?: string;
}

/**
 * Demo identity switcher.
 *
 * Only the *identity* is faked in this product. RBAC is real and deny-by-default from
 * day one (CLAUDE.md §2.4), so this control does exactly one thing: ask the API to issue
 * a signed `naddp_demo_session` cookie for the chosen role. Every authorisation decision
 * that follows is made server-side against that cookie.
 *
 * Failure behaviour is deliberate and load-bearing. `POST /v1/session/assume-role` does
 * not exist yet - the governance track builds it. When the call fails for any reason
 * (missing endpoint, API down, RBAC refusal), this component:
 *
 *   - reverts the visible selection to the role the API last confirmed;
 *   - renders the status code and the API's own message in an inline alert;
 *   - does NOT optimistically pretend the switch happened.
 *
 * A demo that silently shows the wrong identity is far more dangerous in front of a
 * consular audience than one that says plainly that a call failed.
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

  const assumeRole = React.useCallback(
    async (nextRole: NaddpRole): Promise<void> => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setSelected(nextRole);
      setPending(true);
      setError(null);

      try {
        const response = await fetch(`${API_BASE_URL}/v1/session/assume-role`, {
          method: 'POST',
          // The demo session cookie must ride along, and must be settable cross-origin
          // (web on :3000, API on :8000).
          credentials: 'include',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'application/json',
          },
          body: JSON.stringify({ role: nextRole }),
          signal: controller.signal,
        });

        if (!response.ok) {
          let body: unknown;
          try {
            body = await response.json();
          } catch (parseFailure) {
            // A non-JSON error body (a proxy error page, an empty 404) still carries a
            // real status. Preserve it and say why the detail is missing rather than
            // discarding the failure.
            body = {
              detail: `The API returned status ${response.status} with a non-JSON body (${
                parseFailure instanceof Error ? parseFailure.message : 'unknown parse failure'
              }).`,
            };
          }
          throw toApiError(response.status, body);
        }

        setConfirmed(nextRole);
        // Every cached read was scoped to the previous identity. Drop all of it rather
        // than reasoning about which queries happen to be role-sensitive.
        await queryClient.invalidateQueries();
        router.refresh();
      } catch (failure) {
        if (failure instanceof DOMException && failure.name === 'AbortError') {
          // Superseded by a newer selection, or the component unmounted. Not a failure.
          return;
        }
        // Revert to the last identity the API actually confirmed.
        setSelected(confirmed);
        setError(
          failure instanceof TypeError
            ? {
                status: 0,
                message: `Could not reach the API at ${API_BASE_URL}. Is it running?`,
                traceId: null,
              }
            : toApiErrorFromUnknown(failure),
        );
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
        setError({
          status: 0,
          message: `"${value}" is not a recognised role.`,
          traceId: null,
        });
        return;
      }
      void assumeRole(value);
    },
    [assumeRole],
  );

  const labelId = 'role-picker-label';
  const errorId = 'role-picker-error';

  return (
    <div className={cn('relative', className)}>
      <div className="flex items-center gap-2">
        <span id={labelId} className="sr-only">
          Demo identity: choose the role to view the mission as
        </span>

        <Select
          value={selected ?? ''}
          onValueChange={handleChange}
          disabled={pending}
        >
          <SelectTrigger
            aria-labelledby={labelId}
            aria-describedby={error === null ? undefined : errorId}
            aria-invalid={error !== null}
            className={cn(
              'h-8 w-[13.5rem] bg-card text-sm',
              error !== null && 'border-destructive',
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
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
            <LoaderCircle aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />
            Switching
          </span>
        ) : null}
      </div>

      {error === null ? null : (
        <div
          id={errorId}
          role="alert"
          className="absolute right-0 top-[calc(100%+0.5rem)] z-50 w-[22rem] rounded-md border border-destructive/50 bg-popover p-3 text-popover-foreground shadow-lg"
        >
          <p className="flex items-start gap-2 text-sm font-medium">
            <TriangleAlert
              aria-hidden="true"
              className="mt-0.5 h-4 w-4 shrink-0 text-destructive"
            />
            <span>
              Could not switch role
              {error.status > 0 ? ` (HTTP ${error.status})` : ''}
            </span>
          </p>
          <p className="mt-1.5 pl-6 text-sm leading-snug text-muted-foreground">
            {error.message}
          </p>
          {error.traceId === null ? null : (
            <p className="mt-1.5 pl-6 font-mono text-2xs text-muted-foreground">
              trace {error.traceId}
            </p>
          )}
          <p className="mt-2 pl-6 text-2xs leading-snug text-muted-foreground">
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
function toApiErrorFromUnknown(failure: unknown): ApiError {
  if (
    typeof failure === 'object' &&
    failure !== null &&
    'status' in failure &&
    'message' in failure &&
    typeof (failure as { status: unknown }).status === 'number' &&
    typeof (failure as { message: unknown }).message === 'string'
  ) {
    return failure as ApiError;
  }
  return toApiError(0, {
    detail: failure instanceof Error ? failure.message : 'An unexpected error occurred.',
  });
}
