import { afterEach, expect, it, vi } from "vitest";
import type { Page } from "@playwright/test";
import { startReplayMotion } from "../e2e/replay-marker-motion";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

function harness() {
  vi.useFakeTimers();
  let next: FrameRequestCallback | undefined;
  let now = 0;
  const box = { x: 25, y: 1000, width: 40, height: 32 };
  let chartHeight = 480;
  let panelY = 100;
  const button = () => ({
    isConnected: true,
    getAttribute: () => null,
    textContent: "Select private-fill",
    getBoundingClientRect: () => ({ ...box }),
  });
  let marker = button();
  const region = {
    scrollLeft: 0,
    scrollTop: 0,
    contains: (node: unknown) => node === marker,
    querySelectorAll: () => [marker],
    closest: () => ({ getBoundingClientRect: () => ({ y: panelY }) }),
  };

  vi.stubGlobal("window", {});
  vi.stubGlobal("scrollX", 0);
  vi.stubGlobal("scrollY", 0);
  vi.stubGlobal("performance", { now: () => now });
  vi.stubGlobal("document", {
    querySelector: (selector: string) =>
      selector.includes("Actual execution navigation")
        ? region
        : { getBoundingClientRect: () => ({ height: chartHeight }) },
    fonts: { status: "loaded" },
  });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    next = callback;
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {
    next = undefined;
  });
  const page = {
    evaluate: vi.fn(async (callback, arg) => callback(arg)),
  } as unknown as Pick<Page, "evaluate">;
  return {
    page,
    box,
    region,
    frame: (time: number) => {
      now = time;
      const callback = next;
      next = undefined;
      callback?.(time);
      return window.__mineagentReplayMotion!.report;
    },
    changeChart: (amount: number) => {
      chartHeight += amount;
      box.y += amount;
    },
    shiftPanel: (amount: number) => {
      panelY += amount;
      box.y += amount;
    },
    replaceMarker: () => {
      marker.isConnected = false;
      marker = button();
    },
  };
}

it("distinguishes chart-correlated layout, upstream layout, and replaced markers", async () => {
  const h = harness();
  await startReplayMotion(h.page, "Select private-fill");
  expect(h.frame(16)).toMatchObject({ samples: "single" });
  h.changeChart(6);
  expect(h.frame(32)).toMatchObject({
    chart_height_changed: true,
    chart_height_tracks_marker_y: true,
    document_y_changed: true,
    panel_document_y_changed: false,
    late_client_change: false,
  });
  h.replaceMarker();
  h.shiftPanel(9);
  const report = h.frame(1016);
  expect(report).toMatchObject({
    samples: "few",
    matched_samples: "few",
    marker_node_changed: true,
    client_y_changed: true,
    panel_document_y_changed: true,
    panel_y_tracks_marker_y: true,
    late_client_change: true,
    document_scroll_changed: false,
  });
  expect(JSON.stringify(report)).not.toMatch(/private-fill|1016|480/);
});

it("does not attribute viewport-only movement to layout; stops after elapsed bound", async () => {
  const h = harness();
  await startReplayMotion(h.page, "Select private-fill");
  h.frame(16);
  vi.stubGlobal("scrollY", 100);
  h.box.y -= 100;
  h.shiftPanel(-100);
  h.box.y += 100; // only viewport scroll, not document layout
  expect(h.frame(32)).toMatchObject({
    client_y_changed: true,
    document_y_changed: false,
    document_scroll_changed: true,
    region_scroll_changed: false,
    panel_document_y_changed: false,
    chart_height_changed: false,
  });
  h.region.scrollLeft = 1;
  h.frame(16_001);
  expect(h.frame(16_016)).toMatchObject({ region_scroll_changed: false });
  expect(vi.getTimerCount()).toBe(0);
});

it("caps high-refresh sampling without continuing to observe changes", async () => {
  const h = harness();
  await startReplayMotion(h.page, "Select private-fill");
  for (let frame = 1; frame <= 1024; frame++) h.frame(frame);
  h.box.x = 100;
  expect(h.frame(1025)).toMatchObject({
    samples: "many",
    client_x_changed: false,
  });
  expect(vi.getTimerCount()).toBe(0);
});
