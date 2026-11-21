import type { Page } from "@playwright/test";

export type ReplayMotion = {
  samples: "none" | "single" | "few" | "many";
  matched_samples: ReplayMotion["samples"];
  marker_presence_changed: boolean;
  marker_node_changed: boolean;
  client_x_changed: boolean;
  client_y_changed: boolean;
  document_x_changed: boolean;
  document_y_changed: boolean;
  marker_size_changed: boolean;
  late_client_change: boolean;
  chart_height_changed: boolean;
  chart_height_tracks_marker_y: boolean;
  panel_document_y_changed: boolean;
  panel_y_tracks_marker_y: boolean;
  document_scroll_changed: boolean;
  region_scroll_changed: boolean;
  fonts_status_changed: boolean;
};

declare global {
  interface Window {
    __mineagentReplayMotion?: { report: ReplayMotion; stop: () => void };
  }
}

export function startReplayMotion(
  page: Pick<Page, "evaluate">,
  expectedName: string,
) {
  return page.evaluate((name) => {
    window.__mineagentReplayMotion?.stop();
    const report: ReplayMotion = {
      samples: "none",
      matched_samples: "none",
      marker_presence_changed: false,
      marker_node_changed: false,
      client_x_changed: false,
      client_y_changed: false,
      document_x_changed: false,
      document_y_changed: false,
      marker_size_changed: false,
      late_client_change: false,
      chart_height_changed: false,
      chart_height_tracks_marker_y: false,
      panel_document_y_changed: false,
      panel_y_tracks_marker_y: false,
      document_scroll_changed: false,
      region_scroll_changed: false,
      fonts_status_changed: false,
    };
    let frame = 0;
    let matches = 0;
    const bucket = (count: number): ReplayMotion["samples"] =>
      count === 0
        ? "none"
        : count === 1
          ? "single"
          : count < 16
            ? "few"
            : "many";
    let frameId = 0;
    let previous:
      | {
          marker: HTMLButtonElement | null;
          rect: DOMRect | undefined;
          rootX: number;
          rootY: number;
          chartHeight: number | undefined;
          panelY: number | undefined;
          regionX: number | undefined;
          regionY: number | undefined;
          fonts: FontFaceSetLoadStatus | undefined;
        }
      | undefined;
    let lastMarker: HTMLButtonElement | null = null;
    const began = performance.now();
    const stop = () => {
      cancelAnimationFrame(frameId);
      clearTimeout(timer);
    };
    // Sample on the actual animation frames that the ordinary click observes.
    // Both frame/elapsed bounds apply; no arrays, coordinates or DOM text escape.
    const sample = (now: number) => {
      if (now - began > 16_000 || frame >= 1024) return stop();
      frame += 1;
      const region = document.querySelector(
        '[role="region"][aria-label="Actual execution navigation"]',
      );
      let marker = null;
      if (lastMarker?.isConnected && region?.contains(lastMarker)) {
        marker = lastMarker;
      } else {
        const candidates = region?.querySelectorAll("button");
        if (candidates && candidates.length <= 50) {
          const matches = Array.from(candidates).filter(
            (button) =>
              (button.getAttribute("aria-label") ?? button.textContent ?? "")
                .replace(/\s+/g, " ")
                .trim() === name,
          );
          if (matches.length === 1) marker = matches[0];
        }
      }
      const chart = document.querySelector(
        '[role="img"][aria-label="Pinned OHLCV, actual execution markers and declared recorded indicators"]',
      );
      const state = {
        marker,
        rect: marker?.getBoundingClientRect(),
        rootX: scrollX,
        rootY: scrollY,
        chartHeight: chart?.getBoundingClientRect().height,
        // Changes here can indicate reflow above the replay panel (e.g. the
        // independent equity and drawdown charts), rather than its own chart.
        panelY: region?.closest(".panel")?.getBoundingClientRect().y,
        regionX: region?.scrollLeft,
        regionY: region?.scrollTop,
        fonts: document.fonts?.status,
      };
      report.samples = bucket(frame);
      if (marker) report.matched_samples = bucket(++matches);
      if (marker && lastMarker && lastMarker !== marker)
        report.marker_node_changed = true;
      if (marker) lastMarker = marker;
      if (previous) {
        const p = previous;
        report.marker_presence_changed ||=
          Boolean(p.marker) !== Boolean(marker);
        report.document_scroll_changed ||=
          p.rootX !== state.rootX || p.rootY !== state.rootY;
        report.region_scroll_changed ||=
          p.regionX !== undefined &&
          state.regionX !== undefined &&
          (p.regionX !== state.regionX || p.regionY !== state.regionY);
        report.fonts_status_changed ||= p.fonts !== state.fonts;
        const chartDelta =
          p.chartHeight === undefined || state.chartHeight === undefined
            ? 0
            : state.chartHeight - p.chartHeight;
        report.chart_height_changed ||= chartDelta !== 0;
        const panelDelta =
          p.panelY === undefined || state.panelY === undefined
            ? 0
            : state.panelY + state.rootY - (p.panelY + p.rootY);
        report.panel_document_y_changed ||= panelDelta !== 0;
        if (p.rect && state.rect) {
          const clientXChanged = p.rect.x !== state.rect.x;
          const clientYChanged = p.rect.y !== state.rect.y;
          const markerSizeChanged =
            p.rect.width !== state.rect.width ||
            p.rect.height !== state.rect.height;
          const docYDelta = state.rect.y + state.rootY - (p.rect.y + p.rootY);
          report.client_x_changed ||= clientXChanged;
          report.client_y_changed ||= clientYChanged;
          report.document_x_changed ||=
            p.rect.x + p.rootX !== state.rect.x + state.rootX;
          report.document_y_changed ||= docYDelta !== 0;
          report.marker_size_changed ||= markerSizeChanged;
          report.late_client_change ||=
            now - began >= 1_000 &&
            (clientXChanged || clientYChanged || markerSizeChanged);
          report.chart_height_tracks_marker_y ||=
            chartDelta !== 0 &&
            docYDelta !== 0 &&
            Math.abs(chartDelta - docYDelta) <= 1;
          report.panel_y_tracks_marker_y ||=
            panelDelta !== 0 &&
            docYDelta !== 0 &&
            Math.abs(panelDelta - docYDelta) <= 1;
        }
      }
      previous = state;
      frameId = requestAnimationFrame(sample);
    };
    const timer = setTimeout(stop, 16_000);
    window.__mineagentReplayMotion = { report, stop };
    frameId = requestAnimationFrame(sample);
  }, expectedName);
}
