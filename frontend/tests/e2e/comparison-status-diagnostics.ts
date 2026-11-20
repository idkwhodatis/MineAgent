import type { Page } from "@playwright/test";

export const comparisonStatuses = [
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "interrupted",
  "paused",
  "cancel_requested",
  "pause_requested",
  "unrecognized",
] as const;
export const comparisonErrorCodes = [
  "comparison_failed",
  "comparison_cancelled",
  "comparison_interrupted",
  "comparison_timeout",
  "comparison_capacity",
  "comparison_integrity",
  "comparison_algorithm_unsupported",
  "comparison_source_unavailable",
  "comparison_pin_mismatch",
  "comparison_recovery_blocked",
  "comparison_query_too_large",
  "unrecognized",
] as const;
export const comparisonDiagnosticPrefix = "COMPARISON_STATUS_DIAGNOSTIC_V1:";

// One synchronous DOM observation after failure only. No waits, requests, IDs,
// arbitrary text or state changes; private DOM text is classified in the browser.
export async function comparisonStatusDiagnostic(page: Pick<Page, "evaluate">) {
  return page.evaluate(
    ({ statuses, codes }) => {
      const elements = document.querySelectorAll(
        '[data-testid="comparison-status"]',
      );
      const presence =
        elements.length === 0
          ? "missing"
          : elements.length === 1
            ? "single"
            : "multiple";
      let status = "unrecognized";
      let error_code: string | null = null;
      if (presence === "single") {
        const raw = elements[0].textContent;
        if (raw !== null && raw.length <= 64 && statuses.includes(raw.trim()))
          status = raw.trim();
        const notices =
          elements[0].parentElement?.querySelectorAll("p.notice.error");
        if (notices?.length) {
          error_code = "unrecognized";
          if (notices.length === 1) {
            const text = notices[0].textContent;
            if (text !== null && text.length <= 256) {
              const match =
                /^Comparison failed or interrupted: ([a-z_]+)\. No failed run\s+is represented as a zero result\.$/.exec(
                  text.trim(),
                );
              if (match && codes.includes(match[1])) error_code = match[1];
            }
          }
        }
      }
      return { presence, status, error_code };
    },
    {
      statuses: [...comparisonStatuses] as string[],
      codes: [...comparisonErrorCodes] as string[],
    },
  );
}

export async function withComparisonFailureDiagnostic(
  page: Pick<Page, "evaluate">,
  assertion: () => Promise<void>,
) {
  try {
    await assertion();
  } catch (error) {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      // A wedged browser must not replace the original assertion with the
      // overall test timeout. This ceiling applies only to optional observation.
      const ceiling = new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error("diagnostic_timeout")), 250);
      });
      const diagnostic = await Promise.race([
        comparisonStatusDiagnostic(page),
        ceiling,
      ]);
      // Playwright's JUnit reporter retains per-test stdout as system-out.
      // Do not mutate message/stack: cached exception rendering remains intact.
      console.log(comparisonDiagnosticPrefix + JSON.stringify(diagnostic));
    } catch {
      // An observation/annotation failure must never replace the assertion.
    } finally {
      clearTimeout(timer);
    }
    throw error;
  }
}
