import { afterEach, expect, it, vi } from "vitest";
import type { Page } from "@playwright/test";
import {
  replayMarkerDiagnostic,
  replayMarkerPrefix,
  withReplayMarkerFailureDiagnostic,
} from "../e2e/replay-marker-diagnostics";

const expected = {
  name: "Select private-fill-id",
  listing: "private-listing-id",
  start: "2026-01-01",
  end: "2026-01-30",
};

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function page({
  count = 1,
  table = true,
  blocked = false,
  listing = expected.listing,
  x = 5,
  y = 5,
  tableWidth = 1200,
  tableHeight = 800,
} = {}) {
  const region = {
    getBoundingClientRect: () => ({ x: 0, y: 0 }),
    clientLeft: 0,
    clientTop: 0,
    clientWidth: tableWidth,
    clientHeight: tableHeight,
  };
  const button = {
    getAttribute: () => null,
    textContent: `  ${expected.name}  `,
    getBoundingClientRect: () => ({ x, y, width: 10, height: 8 }),
    closest: () => region,
    matches: () => false,
    contains: (hit: unknown) => hit === button,
  };
  const buttons = Array.from({ length: count }, () => button);
  vi.stubGlobal("document", {
    querySelectorAll: (selector: string) => {
      if (selector === "button") return buttons;
      if (selector === "select#replay-listing") return [{ value: listing }];
      if (selector.includes("data-testid")) {
        return [
          {
            textContent: `Replay window [${expected.start}, ${expected.end}) · fixture`,
          },
        ];
      }
      return table ? [{ querySelectorAll: () => buttons }] : [];
    },
    elementFromPoint: () => (blocked ? {} : button),
  });
  vi.stubGlobal("getComputedStyle", () => ({ visibility: "visible" }));
  vi.stubGlobal("innerWidth", 1200);
  vi.stubGlobal("innerHeight", 800);
  return { evaluate: vi.fn(async (fn, arg) => fn(arg)) } as unknown as Pick<
    Page,
    "evaluate"
  >;
}

it("reports post-failure DOM geometry and only equality booleans for private values", async () => {
  const result = await replayMarkerDiagnostic(
    page({ blocked: true, listing: "different" }),
    expected,
  );
  expect(result).toEqual({
    table: "single",
    page_matches: "single",
    table_matches: "single",
    listing_matches: false,
    window_matches: true,
    target: "center_blocked",
    viewport_edge: null,
    table_center_clipped: false,
  });
  expect(JSON.stringify(result)).not.toMatch(/private|different|2026/);
});

it("distinguishes missing and duplicate buttons, and absent table", async () => {
  expect(
    await replayMarkerDiagnostic(page({ count: 0 }), expected),
  ).toMatchObject({
    page_matches: "missing",
    table_matches: "missing",
    target: "not_unique",
  });
  expect(
    await replayMarkerDiagnostic(page({ count: 2 }), expected),
  ).toMatchObject({
    page_matches: "multiple",
    table_matches: "multiple",
    target: "not_unique",
  });
  expect(
    await replayMarkerDiagnostic(page({ table: false }), expected),
  ).toMatchObject({
    table: "missing",
    page_matches: "single",
    table_matches: null,
    target: "not_unique",
  });
});

it("executes one original click and never snapshots success", async () => {
  const p = page();
  const click = vi.fn(async () => {});
  await withReplayMarkerFailureDiagnostic(p, expected, click);
  expect(click).toHaveBeenCalledTimes(1);
  expect(p.evaluate).not.toHaveBeenCalled();
});

it("logs closed evidence once and keeps the identical original failure", async () => {
  const p = page();
  const log = vi.spyOn(console, "log").mockImplementation(() => {});
  const original = new Error("private click error");
  const click = vi.fn(async () => {
    throw original;
  });
  await expect(
    withReplayMarkerFailureDiagnostic(p, expected, click),
  ).rejects.toBe(original);
  expect(click).toHaveBeenCalledTimes(1);
  expect(p.evaluate).toHaveBeenCalledTimes(1);
  expect(log).toHaveBeenCalledWith(
    replayMarkerPrefix +
      JSON.stringify({
        table: "single",
        page_matches: "single",
        table_matches: "single",
        listing_matches: true,
        window_matches: true,
        target: "center_hits_button",
        viewport_edge: null,
        table_center_clipped: false,
        click_report: "unreported",
      }),
  );
});

it("bounds a wedged snapshot without retrying or replacing the failure", async () => {
  vi.useFakeTimers();
  const log = vi.spyOn(console, "log").mockImplementation(() => {});
  const p = { evaluate: vi.fn(() => new Promise(() => {})) } as unknown as Pick<
    Page,
    "evaluate"
  >;
  const original = new Error("private click error");
  const click = vi.fn(async () => {
    throw original;
  });
  const checked = expect(
    withReplayMarkerFailureDiagnostic(p, expected, click),
  ).rejects.toBe(original);
  await vi.advanceTimersByTimeAsync(250);
  await checked;
  expect(click).toHaveBeenCalledTimes(1);
  expect(p.evaluate).toHaveBeenCalledTimes(1);
  expect(log).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});

it.each([
  [{ x: 10, y: 820, tableHeight: 1000 }, "below", false],
  [{ x: 1250, y: 10 }, "right", true],
  [{ x: -100, y: 900 }, "both_axes", true],
  [{ x: 12, y: 12, tableWidth: 10 }, null, true],
] as const)(
  "distinguishes viewport edge and table center clipping %j",
  async (geometry, edge, clipped) => {
    expect(
      await replayMarkerDiagnostic(page(geometry), expected),
    ).toMatchObject({
      viewport_edge: edge,
      table_center_clipped: clipped,
    });
  },
);

it.each([
  [
    "locator.click: Error: strict mode violation: private descriptor",
    "strict_locator",
  ],
  [
    "locator.click: Timeout private-number\nCall log:\n  - waiting for getByRole(private-locator)",
    "waiting_for_locator",
  ],
  [
    "locator.click: Timeout private-number\nCall log:\n  - waiting for getByRole(private-locator)\n    - waiting for element to be visible, enabled and stable",
    "waiting_for_actionability",
  ],
  [
    "locator.click: Timeout private-number\nCall log:\n\u001b[2m  - waiting for getByRole(private-locator)\n    15 × element is outside of the viewport\n    - scrolling into view if needed\n    - done scrolling\u001b[22m",
    "outside_viewport",
  ],
  [
    "locator.click: Timeout private-number\nCall log:\n  - waiting for getByRole(private-locator)\n    - <div>private-target</div> intercepts pointer events",
    "intercepted",
  ],
  [
    "locator.click: Timeout private-number\nCall log:\n  - waiting for getByRole(private-locator)\n    - performing click action\n    - click action done",
    "click_performed",
  ],
] as const)(
  "publishes only a bounded Playwright state %#",
  async (message, report) => {
    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    const original = new Error(message);
    await expect(
      withReplayMarkerFailureDiagnostic(page(), expected, async () => {
        throw original;
      }),
    ).rejects.toBe(original);
    const [logged] = log.mock.calls[0];
    expect(logged).toContain(`"click_report":"${report}"`);
    expect(logged).not.toContain("private");
  },
);
