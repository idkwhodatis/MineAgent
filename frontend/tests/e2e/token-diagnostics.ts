// Test-only annotation. No response body, credentials, URL or exception text is
// inspected. The same thrown value propagates; the private stack is projected
// independently by the Python allowlist before any CI output is published.
export const tokenDiagnosticPrefix = "TOKEN_DIAGNOSTIC_V1:";
export type TokenPhase = "request" | "http" | "decode";
export function tokenDiagnostic(phase: TokenPhase, status: number | null) {
  const http_status =
    Number.isInteger(status) &&
    status !== null &&
    status >= 100 &&
    status <= 599
      ? status
      : null;
  return tokenDiagnosticPrefix + JSON.stringify({ phase, http_status });
}
export async function observeTokenPhase<T>(
  phase: TokenPhase,
  status: number | null,
  operation: () => Promise<T>,
): Promise<T> {
  try {
    return await operation();
  } catch (error) {
    try {
      if (error instanceof Error) {
        // Prefix, rather than serialize, the original error. Frozen/foreign
        // thrown values remain untouched and must not mask the original failure.
        const marker = tokenDiagnostic(phase, status);
        const originalStack = error.stack;
        error.message += "\n" + marker;
        error.stack = marker + "\n" + originalStack;
      }
    } catch {
      // Diagnostics must never replace the original failure.
    }
    throw error;
  }
}
