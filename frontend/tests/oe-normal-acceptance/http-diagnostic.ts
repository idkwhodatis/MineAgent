/** Closed diagnostic vocabulary: never return response bodies, messages or IDs. */
export function submitHttpDiagnostic(status: unknown, body: unknown): string {
  const statuses = [200, 201, 400, 401, 403, 404, 409, 413, 422, 429, 500, 503];
  const codes = [
    "preflight_changed",
    "run_quota",
    "database_unavailable",
    "internal_error",
    "validation_error",
    "invalid_oe_structured_request",
    "invalid_request",
    "invalid_idempotency_key",
    "idempotency_conflict",
    "neutral_busy",
    "runtime_schema_unavailable",
    "structured_snapshot_release_required",
  ];
  const code =
    body !== null && typeof body === "object"
      ? (body as { code?: unknown }).code
      : undefined;
  return `OE_SUBMIT_HTTP_V1:${typeof status === "number" && statuses.includes(status) ? status : "other"}:${typeof code === "string" && codes.includes(code) ? code : "other"}`;
}
