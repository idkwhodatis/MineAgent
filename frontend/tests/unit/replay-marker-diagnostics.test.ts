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
} = {}) {
  const button = {
    getAttribute: () => null,
    textContent: `  ${expected.name}  `,
    getBoundingClientRect: () => ({ x: 5, y: 5, width: 10, height: 8 }),
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
