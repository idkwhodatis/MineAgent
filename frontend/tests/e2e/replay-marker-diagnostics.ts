import type { Page } from "@playwright/test";

export const replayMarkerPrefix = "REPLAY_MARKER_DIAGNOSTIC_V2:";

type ReplayMarkerExpectation = {
  name: string;
  listing: string;
  start: string;
  end: string;
};

// Project Playwright's existing call log into fixed labels; never print it. A
// millisecond budget, locator, hit target or exception text cannot escape.
function clickReport(error: unknown) {
  if (!(error instanceof Error)) return "unreported";
  const message = error.message.slice(0, 16384);
  if (/^locator\.click: (?:Error: )?strict mode violation:/.test(message))
    return "strict_locator";
  const callLog = message.split(/^Call log:\r?$/m, 2)[1];
  if (!callLog) return "unreported";
  let report = "unreported";
  for (const entry of callLog.split(/\r?\n/, 192)) {
    const line = entry
      .replaceAll("\u001b[2m", "")
      .replaceAll("\u001b[22m", "")
      .trim()
      .replace(/^\d{1,5} × /, "- ");
    if (line.startsWith("- waiting for getByRole(")) {
      report = "waiting_for_locator";
    } else if (
      line === "- waiting for element to be visible, enabled and stable"
    ) {
      if (report === "waiting_for_locator" || report === "unreported")
        report = "waiting_for_actionability";
    } else if (line === "- element was detached from the DOM, retrying") {
      report = "detached";
    } else if (line === "- element is outside of the viewport") {
      report = "outside_viewport";
    } else if (
      line.endsWith(" intercepts pointer events") &&
      line.startsWith("- ")
    ) {
      report = "intercepted";
    } else if (line === "- scrolling into view if needed") {
      if (report === "waiting_for_actionability" || report === "unreported")
        report = "scroll_pending";
    } else if (line === "- done scrolling") {
      if (report === "scroll_pending" || report === "unreported")
        report = "scrolled";
    } else if (line === "- performing click action") {
      report = "performing_click";
    } else if (line === "- click action done") {
      report = "click_performed";
    }
  }
  return report;
}

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
    let viewportEdge = null;
    let tableCenterClipped = null;
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
        const region = point.closest(
          '[role="region"][aria-label="Actual execution navigation"]',
        );
        if (region) {
          const clippingRect = region.getBoundingClientRect();
          const left = clippingRect.x + region.clientLeft;
          const top = clippingRect.y + region.clientTop;
          tableCenterClipped =
            x < left ||
            x >= left + region.clientWidth ||
            y < top ||
            y >= top + region.clientHeight;
        }
        const horizontal = x < 0 ? "left" : x >= innerWidth ? "right" : null;
        const vertical = y < 0 ? "above" : y >= innerHeight ? "below" : null;
        if (horizontal || vertical) {
          target = "outside_viewport";
          viewportEdge =
            horizontal && vertical ? "both_axes" : (horizontal ?? vertical);
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
      viewport_edge: viewportEdge,
      table_center_clipped: tableCenterClipped,
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
      console.log(
        replayMarkerPrefix +
          JSON.stringify({ ...diagnostic, click_report: clickReport(error) }),
      );
    } catch {
      // Preserve the original click failure even if observation fails.
    } finally {
      clearTimeout(timer);
    }
    throw error;
  }
}
