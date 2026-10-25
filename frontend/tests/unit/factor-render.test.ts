import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
vi.mock("../../src/lib/api", () => ({
  describeError: (error: Error) => ({ message: error.message }),
}));
import {
  FactorUsagePanel,
  FactorEvaluationPanel,
  SavedCalculation,
} from "../../src/features/factor-workbench/panels";
import { exactSourceHash } from "../../src/features/factor-workbench/editor";
import type {
  FactorCalculationSample,
  FactorDetail,
} from "../../src/lib/factor-api";

import { exposureClassificationNotice } from "../../src/features/final-tests/exposure";

describe("factor evidence presentation", () => {
  it("does not describe unknown imported exposure history as a clean holdout", () => {
    expect(exposureClassificationNotice("unknown_import_history")).toContain(
      "do not treat this interval as a clean holdout",
    );
    expect(exposureClassificationNotice("no_tracked_overlap")).toBeNull();
    expect(exposureClassificationNotice("previously_exposed")).toBeNull();
  });
  it("keeps usage counts separate and does not claim reads caused decisions", () => {
    const html = renderToStaticMarkup(
      createElement(FactorUsagePanel, {
        usage: {
          declared: 0,
          explicit_read: 0,
          experiment: 0,
          observation_coverage: "not_instrumented",
        },
      }),
    );
    expect(html).toContain("Declared dependencies");
    expect(html).toContain("Explicit host-recorded reads");
    expect(html).toContain("Controlled experiments");
    expect(html).toContain("do not prove causal contribution");
  });
  it("displays not evaluated rather than zero-valued financial metrics", () => {
    const html = renderToStaticMarkup(
      createElement(FactorEvaluationPanel, {
        detail: { evaluations: [] } as unknown as FactorDetail,
      }),
    );
    expect(html).toContain("Not evaluated");
    expect(html).toContain("No Qlib metric is inferred");
    expect(html).not.toContain("IC: 0");
  });
  it("renders saved zero inputs and explicit zero-volatility missing output without coercion", () => {
    const sample = {
      factor_version_id: "v",
      listing_id: "AAA",
      session_date: "2026-03-08",
      operator: "risk_adjusted_momentum",
      inputs: [
        {
          listing_id: "AAA",
          session_date: "2026-03-08",
          close: 0,
          available_at: "2026-03-08T21:00:00Z",
        },
      ],
      peer_scores: [],
      explanation_source: "fixed_fixture_recomputed",
      output: {
        event_time: "2026-03-08T21:00:00Z",
        session_date: "2026-03-08",
        listing_id: "AAA",
        factor_version_id: "v",
        input_evidence_digest: "a".repeat(64),
        value: null,
        value_state: "calculation_missing",
        missing_reason: "zero_daily_volatility",
        available_at: "2026-03-08T21:00:00Z",
        input_window_start: "2026-03-06",
        input_window_end: "2026-03-09",
      },
    } as FactorCalculationSample;
    const html = renderToStaticMarkup(
      createElement(SavedCalculation, { sample }),
    );
    expect(html).toContain("zero_daily_volatility");
    expect(html).toContain("Actual saved input window");
    expect(html).toContain("<td>0</td>");
    expect(html).toContain("fixed_fixture_recomputed");
  });
  it("hashes exact source bytes without normalizing whitespace or line endings", async () => {
    expect(await exactSourceHash("x\n")).not.toBe(
      await exactSourceHash("x\r\n"),
    );
    await expect(exactSourceHash(" ")).rejects.toThrow("nonempty");
    await expect(exactSourceHash("é".repeat(65537))).rejects.toThrow("128 KiB");
  });
});
