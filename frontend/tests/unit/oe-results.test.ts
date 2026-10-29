import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import type {
  NormalExecutionRecord,
  NormalExecutionView,
  NormalPairComparison,
} from "../../src/client/types.gen";
import {
  exactNormalResult,
  exactNormalPair,
  releaseRequired,
} from "../../src/lib/oe-results";
import { OENormalResultPanel } from "../../src/features/oe-provider-results";

const record = {
  candidate_id: "candidate",
  parent_candidate_id: null,
  execution_id: "execution",
  program_version_id: "program",
  snapshot_id: "snapshot",
  source_run_id: "source",
} as NormalExecutionRecord;
const value = {
  candidate_id: "candidate",
  qualification: "fixed_snapshot_comparison_not_causal_gain",
  execution: {
    run_id: "run",
    execution_id: "execution",
    structured_strategy_version_id: "program",
    execution_snapshot_id: "snapshot",
    execution_snapshot_run_id: "source",
    result_sha256: "a",
    market_sha256: "b",
    decisions: [],
    orders: [],
    fills: [],
    factor_definition: { lookback_sessions: 2 },
  },
  measurement: {
    execution_id: "execution",
    structured_strategy_version_id: "program",
    snapshot_id: "snapshot",
    result_sha256: "a",
    market_sha256: "b",
    eligible: false,
    reason: "undefined_accounting",
    combined_score: null,
    total_return_pct: null,
    max_drawdown_pct: null,
    closed_trade_count: 0,
  },
} as unknown as NormalExecutionView;
afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});
it("only exact release-required409 presents disclosure controls", () => {
  expect(
    releaseRequired({ status: 409, code: "engine_result_release_required" }),
  ).toBe(true);
  for (const item of [
    null,
    {},
    { status: 404, code: "engine_result_release_required" },
    { status: 409, code: "integrity_failed" },
  ])
    expect(releaseRequired(item)).toBe(false);
});
it.each([
  "run",
  "candidate",
  "execution",
  "program",
  "snapshot",
  "source",
  "measurement",
])("rejects released identity substitution %s", (field) => {
  const changed = structuredClone(value);
  if (field === "run") changed.execution.run_id = "other";
  if (field === "candidate") changed.candidate_id = "other";
  if (field === "execution") changed.execution.execution_id = "other";
  if (field === "program")
    changed.execution.structured_strategy_version_id = "other";
  if (field === "snapshot") changed.execution.execution_snapshot_id = "other";
  if (field === "source") changed.execution.execution_snapshot_run_id = "other";
  if (field === "measurement") changed.measurement.result_sha256 = "other";
  expect(() => exactNormalResult(changed, "run", record)).toThrow();
});
it("permits only a direct parent-child pair on the same snapshot", () => {
  const childRecord = {
    ...record,
    candidate_id: "child",
    parent_candidate_id: "candidate",
    execution_id: "child-execution",
  };
  const child = structuredClone(value);
  child.candidate_id = "child";
  child.execution.execution_id = "child-execution";
  child.measurement.execution_id = "child-execution";
  const pair = {
    before: value,
    after: child,
    score_delta_percentage_points: null,
    qualification: "fixed_snapshot_comparison_not_causal_gain",
  } as NormalPairComparison;
  expect(exactNormalPair(pair, "run", record, childRecord)).toBe(pair);
  expect(() =>
    exactNormalPair(pair, "run", record, {
      ...childRecord,
      parent_candidate_id: "unrelated",
    }),
  ).toThrow();
  expect(() =>
    exactNormalPair(pair, "run", record, {
      ...childRecord,
      snapshot_id: "different",
    }),
  ).toThrow();
});
it("renders actual ineligibility and empty observed orders without inventing fills or zero score", () => {
  const html = renderToStaticMarkup(
    createElement(OENormalResultPanel, { value, side: "Selected" }),
  );
  expect(html).toContain("ineligible — no score");
  expect(html).toContain("No observed orders");
  expect(html).toContain("No observed fills");
  expect(html).toContain("Targets are not observed fills");
  expect(html).not.toContain("Combined score: 0");
});
it.each(["correct", "wrong_execution", "duplicate_exposure"])(
  "generated release transport validates exact receipt %s",
  async (mode) => {
    vi.stubGlobal("window", { location: { origin: "https://example.test" } });
    vi.stubGlobal("document", {
      querySelector: () => ({ content: "/research" }),
    });
    vi.stubGlobal("sessionStorage", {
      getItem: () =>
        JSON.stringify({
          accessToken: "owner-token",
          expiresAt: Date.now() + 60000,
        }),
    });
    const receipt = {
      run_id: "run",
      execution_id: mode === "wrong_execution" ? "wrong" : "execution",
      exposure_ids:
        mode === "duplicate_exposure" ? ["one", "one"] : ["one", "two"],
      qualification: "exposure_record_not_execution_authority",
      extent: "full_retained_factor_and_market_datasets",
    };
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(receipt), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetcher);
    const { oeRelease } = await import("../../src/lib/oe-results");
    if (mode === "correct")
      expect(await oeRelease("run", "execution")).toEqual(receipt);
    else
      await expect(oeRelease("run", "execution")).rejects.toThrow(
        "receipt differs",
      );
    const request = fetcher.mock.calls[0][0] as Request;
    expect(request.method).toBe("POST");
    expect(request.url).toBe(
      "https://example.test/research/api/v1/engine-runs/structured-search/runs/run/executions/execution/release",
    );
    expect(request.headers.get("Authorization")).toBe("Bearer owner-token");
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
