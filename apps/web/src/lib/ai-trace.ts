import type { AiTrace, BriefTrace } from '@naddp/contracts';

/**
 * An `ai_traces` row, in the shape `TraceBadge` and its drawer already render.
 *
 * `GET /v1/ai/traces/{trace_id}` returns the full row; the badge and drawer read the routing
 * subset that the brief and pre-read embed. Every field is copied, none is derived: the badge
 * string in particular stays exactly as the Gateway wrote it at stage 5.
 */
export function asBriefTrace(trace: AiTrace): BriefTrace {
  return {
    trace_id: trace.id,
    route_badge: trace.route_badge,
    data_class: trace.data_class,
    result_class: trace.result_class,
    model_route: trace.model_route,
    route_reason: trace.route_reason,
    model_requested: trace.model_requested,
    model_used: trace.model_used,
    fallback: trace.fallback,
    fallback_reason: trace.fallback_reason,
  };
}
