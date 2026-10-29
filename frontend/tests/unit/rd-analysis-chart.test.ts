/** Presentation/selection tests, not runtime analysis or real browser acceptance. */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type {
  DirectAnalysisResult,
  DirectDay,
} from "../../src/client/types.gen";
import {
  RDAnalysisChart,
  analysisObservation,
  observationForAnalysis,
  correlationSegments,
  toggleAnalysisSession,
} from "../../src/features/rd-analysis-chart";
const query = vi.hoisted(() => ({
  data: undefined as DirectAnalysisResult | undefined,
  error: null as unknown,
  isPending: false,
}));
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ ...query, refetch: vi.fn() }),
  queryOptions: (value: unknown) => value,
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: unknown }) => children,
}));
vi.mock("../../src/lib/api", () => ({
  describeError: (error: Error) => ({ message: error.message }),
}));
import { DirectAnalysisPanel } from "../../src/features/engine-results";
const days: DirectDay[] = [
  {
    session: "2026-01-01",
    pair_count: 4,
    ic: 0.5,
    rank_ic: -0.5,
    reason: "defined",
  },
  {
    session: "2026-01-02",
    pair_count: 1,
    ic: null,
    rank_ic: null,
    reason: "insufficient_pairs",
  },
  {
    session: "2026-01-03",
    pair_count: 4,
    ic: 0,
    rank_ic: 1,
    reason: "defined",
  },
  {
    session: "2026-01-04",
    pair_count: 4,
    ic: null,
    rank_ic: null,
    reason: "constant_cross_section",
  },
];
const analysis: DirectAnalysisResult = {
  analysis_id: "analysis",
  run_id: "run",
  execution_snapshot_id: "snapshot",
  exposure_ids: ["exposure"],
  result_sha256: "result-hash",
  measurement_sha256: "measurement-hash",
  result: {
    days,
    defined_days: 2,
    ic: 0.25,
    rank_ic: 0.25,
    total_pairs: 13,
    protocol_sha256: "protocol-hash",
  },
  measurement: {
    status: "passed",
    reason: "threshold_satisfied",
    declaration_sha256: "declaration-hash",
    result_sha256: "result-hash",
    protocol_sha256: "protocol-hash",
    defined_days: 2,
    defined_pairs: 8,
    total_pairs: 13,
    value: 0.25,
  },
};
const render = (value = analysis, callback?: () => void) =>
  renderToStaticMarkup(
    createElement(RDAnalysisChart, {
      analysis: value,
      onObservation: callback,
    }),
  );
describe("retained RD daily diagnostics", () => {
  it("separates null gaps and preserves real zero observations", () => {
    const segments = correlationSegments(days, "ic");
    expect(segments).toHaveLength(2);
    expect(segments.every((segment) => !segment.includes(" "))).toBe(true);
    expect(segments[1]).toContain(",90");
    expect(correlationSegments(days, "rank_ic")).toHaveLength(2);
  });
  it("never draws nonfinite or out-of-range correlations as measurements", () => {
    expect(
      correlationSegments(
        [
          { ...days[0], ic: Number.NaN },
          { ...days[2], ic: 2 },
        ],
        "ic",
      ),
    ).toEqual([]);
  });
  it("renders actual correlations and counts with a complete tabular alternative", () => {
    const html = render();
    expect(html).toContain('aria-label="Daily IC"');
    expect(html).toContain('aria-label="Daily rank IC"');
    expect(html).toContain('aria-label="Daily paired observations"');
    for (const day of days) expect(html).toContain(day.session);
    expect(html).toContain("Not defined");
    expect(html).toContain("constant_cross_section");
    expect(html).toContain("insufficient_pairs");
    expect(html).toContain("Group-label spread: not computed");
    expect(html).toContain("no denominator is inferred");
    expect(html).not.toContain("Use session");
  });
  it("enables explicit bounded session context controls only when connected", () => {
    const callback = vi.fn();
    const html = render(analysis, callback);
    expect(html).toContain("Use session 2026-01-01 as guidance context");
    expect(html).toContain("Prepare guidance from selected sessions");
    expect(html).toContain("disabled");
    expect(callback).not.toHaveBeenCalled();
  });
  it("uses only retained selected dates and exact immutable identities", () => {
    expect(
      analysisObservation(analysis, [
        "unloaded",
        days[2].session,
        days[0].session,
      ]),
    ).toEqual({
      kind: "released_rd_direct_analysis",
      refs: {
        run_id: "run",
        analysis_id: "analysis",
        execution_snapshot_id: "snapshot",
        result_sha256: "result-hash",
        measurement_sha256: "measurement-hash",
        protocol_sha256: "protocol-hash",
      },
      sessions: [days[0].session, days[2].session],
    });
    expect(analysisObservation(analysis, []).sessions).toEqual([]);
    expect(
      analysisObservation(analysis, [days[0].session]).refs,
    ).not.toHaveProperty("asset_id");
  });
  it("bounds graph and table selection consistently, without applying guidance", () => {
    const full = Array.from({ length: 32 }, (_, i) => `session-${i}`);
    expect(toggleAnalysisSession(full, "new-session")).toBe(full);
    expect(toggleAnalysisSession(full, "session-1")).toHaveLength(31);
    expect(toggleAnalysisSession([], "session-1")).toEqual(["session-1"]);
    const html = render(analysis, () => {});
    expect(html).toContain("Select 2026-01-01: Daily IC 0.5");
    expect(html).toContain('role="button"');
    expect(html).toContain('tabindex="0"');
  });
  it("bounds context even if called outside the checkbox controls", () => {
    const many = Array.from({ length: 100 }, (_, i) => ({
      ...days[0],
      session: `session-${i}`,
    }));
    expect(() =>
      analysisObservation(
        { ...analysis, result: { ...analysis.result, days: many } },
        many.map((day) => day.session),
      ),
    ).toThrow("no more than 32");
  });
  it("shows empty records explicitly without fabricated zero charts", () => {
    const html = render({
      ...analysis,
      result: { ...analysis.result, days: [] },
    });
    expect(html).toContain("No retained daily observations");
    expect(html).not.toContain("<svg");
  });
  it("does not render charts before release or on identity mismatch", () => {
    query.data = undefined;
    query.error = { code: "engine_result_release_required" };
    let html = renderToStaticMarkup(
      createElement(DirectAnalysisPanel, {
        runId: "run",
        analysisId: "analysis",
      }),
    );
    expect(html).toContain("Explicit historical result release required");
    expect(html).not.toContain("Daily IC");
    query.error = null;
    query.data = { ...analysis, execution_snapshot_id: "wrong" };
    html = renderToStaticMarkup(
      createElement(DirectAnalysisPanel, {
        runId: "run",
        analysisId: "analysis",
        expectedSnapshotId: "snapshot",
      }),
    );
    expect(html).toContain("does not match");
    expect(html).not.toContain("Daily IC");
    query.data = analysis;
    html = renderToStaticMarkup(
      createElement(DirectAnalysisPanel, {
        runId: "run",
        analysisId: "analysis",
        expectedSnapshotId: "snapshot",
      }),
    );
    expect(html).toContain("Daily IC");
    query.data = undefined;
  });
});

// The component uses this guard before rendering any retained draft after a route/query change.
it("invalidates a retained draft on every loaded analysis identity or hash change", () => {
  const value = analysis;
  const observation = analysisObservation(value, [
    value.result.days[0].session,
  ]);
  expect(observationForAnalysis(observation, value)).toBe(observation);
  for (const field of [
    "run_id",
    "analysis_id",
    "execution_snapshot_id",
    "result_sha256",
    "measurement_sha256",
  ] as const) {
    expect(
      observationForAnalysis(observation, { ...value, [field]: "different" }),
    ).toBeUndefined();
  }
  expect(
    observationForAnalysis(observation, {
      ...value,
      result: { ...value.result, protocol_sha256: "different" },
    }),
  ).toBeUndefined();
  expect(observationForAnalysis(undefined, value)).toBeUndefined();
});
