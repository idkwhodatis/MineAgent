/** Acceptance-only projection: no identifiers, hashes, paths or exception strings. */
import { factorResponseCode } from "./factor-import-retry";

export function importStatusDiagnostic(
  httpStatus: unknown,
  body: unknown,
): string {
  const value =
    body !== null && typeof body === "object" && !Array.isArray(body)
      ? (body as Record<string, unknown>)
      : {};
  const statuses = new Set([
    "queued",
    "processing",
    "succeeded",
    "failed",
    "blocked",
  ]);
  const status =
    typeof value.status === "string" && statuses.has(value.status)
      ? value.status
      : "unknown";
  const generation =
    typeof value.generation === "number" &&
    Number.isInteger(value.generation) &&
    value.generation >= 0 &&
    value.generation <= 3
      ? String(value.generation)
      : "unknown";
  const http =
    typeof httpStatus === "number" &&
    Number.isInteger(httpStatus) &&
    httpStatus >= 100 &&
    httpStatus <= 599
      ? String(httpStatus)
      : "none";
  const error = factorResponseCode({ code: value.error_code ?? value.code });
  const envelope =
    typeof value.result_envelope_id === "string" &&
    value.result_envelope_id.length > 0;
  const binding =
    value.local_bindings !== null &&
    typeof value.local_bindings === "object" &&
    !Array.isArray(value.local_bindings);
  return `FACTOR_IMPORT_STATUS_DIAGNOSTIC_V1:http=${http};status=${status};error=${error};generation=${generation};envelope=${envelope};binding=${binding}`;
}

/** Exactly one existing-owner read, only after the unchanged wait has failed. */
export async function observeFailedImport(
  read: () => Promise<{ status(): number; json(): Promise<unknown> }>,
): Promise<string> {
  try {
    const response = await read();
    return importStatusDiagnostic(
      response.status(),
      await response.json().catch(() => null),
    );
  } catch {
    return importStatusDiagnostic(null, null);
  }
}
