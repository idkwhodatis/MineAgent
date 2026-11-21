import type { Page } from "@playwright/test";

export const replayMarkerPrefix = "REPLAY_MARKER_DIAGNOSTIC_V1:";

type ReplayMarkerExpectation = {
  name: string;
  listing: string;
  start: string;
  end: string;
};

// A snapshot after the original click fails, not a replacement for Playwright's
// actionability rules. Classify inside the browser; no DOM text leaves it.
export function replayMarkerDiagnostic(
  page: Pick<Page, "evaluate">,
  expected: ReplayMarkerExpectation,
) {
  return page.evaluate((expected) => {
    const presence = (size: number) =>
      size === 0 ? "missing" : size === 1 ? "single" : "multiple";
    const tables = document.querySelectorAll(
      '[role="region"][aria-label="Actual execution navigation"] table',
    );
    const matching = (scope: ParentNode) =>
      Array.from(scope.querySelectorAll("button")).filter(
        (button) =>
          (button.getAttribute("aria-label") ?? button.textContent ?? "")
            .replace(/\s+/g, " ")
            .trim() === expected.name,
      );
    const onPage = matching(document);
    const inTable = tables.length === 1 ? matching(tables[0]) : null;
    const listing = document.querySelectorAll<HTMLSelectElement>(
      "select#replay-listing",
    );
    const window = document.querySelectorAll('[data-testid="replay-window"]');
    const point = inTable?.length === 1 ? inTable[0] : null;
    let target = "not_unique";
    if (point) {
      const rect = point.getBoundingClientRect();
      if (
        !rect.width ||
        !rect.height ||
        getComputedStyle(point).visibility !== "visible"
      ) {
        target = "not_visible";
      } else if (
        point.matches(":disabled") ||
        point.getAttribute("aria-disabled") === "true"
      ) {
        target = "disabled";
      } else {
        const x = rect.x + rect.width / 2;
        const y = rect.y + rect.height / 2;
        if (x < 0 || x >= innerWidth || y < 0 || y >= innerHeight) {
          target = "outside_viewport";
        } else {
          const hit = document.elementFromPoint(x, y);
          target =
            hit && point.contains(hit)
              ? "center_hits_button"
              : "center_blocked";
        }
      }
    }
    return {
      table: presence(tables.length),
      page_matches: presence(onPage.length),
      table_matches: inTable === null ? null : presence(inTable.length),
      listing_matches:
        listing.length === 1 ? listing[0].value === expected.listing : null,
      window_matches:
        window.length === 1
          ? (window[0].textContent ?? "").startsWith(
              `Replay window [${expected.start}, ${expected.end})`,
            )
          : null,
      target,
    };
  }, expected);
}

export async function withReplayMarkerFailureDiagnostic(
  page: Pick<Page, "evaluate">,
  expected: ReplayMarkerExpectation,
  click: () => Promise<void>,
) {
  try {
    await click();
  } catch (error) {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      // Cap only the optional post-failure snapshot, not the real click.
      const ceiling = new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error("diagnostic_timeout")), 250);
      });
      const diagnostic = await Promise.race([
        replayMarkerDiagnostic(page, expected),
        ceiling,
      ]);
      console.log(replayMarkerPrefix + JSON.stringify(diagnostic));
    } catch {
      // Preserve the original click failure even if observation fails.
    } finally {
      clearTimeout(timer);
    }
    throw error;
  }
}
