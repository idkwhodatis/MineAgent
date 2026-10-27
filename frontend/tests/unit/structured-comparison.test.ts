/** Portable presentation only: no database, release, browser or runtime proof. */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import {
  StructuredComparisonPanel,
  StructuredExecutionPanel,
} from "../../src/features/structured-comparison";
import type {
  StructuredExecution,
  StructuredComparison,
} from "../../src/lib/structured-comparison";
const hash = "a".repeat(64);
const state = {
  payload_json: "{}",
  payload_sha256: hash,
  schema_name: "portable-test",
  schema_sha256: hash,
  schema_version: 1,
};
const execution: StructuredExecution = {
  schema_version: "mineagent.structured-execution-view.v1",
  execution_id: "execution-a",
  run_id: "run-a",
  structured_strategy_version_id: "strategy-a",
  factor_id: "factor-a",
  factor_version_id: "version-a",
  parent_factor_version_id: null,
  execution_snapshot_id: "snapshot-a",
  execution_snapshot_run_id: "source-run-a",
  factor_definition: {
    code_sha256: hash,
    factor_kind: "time_series",
    input_fields: [
      { name: "close", price_semantics: "unadjusted", unit: "currency" },
    ],
    lookback_sessions: 2,
    output_unit: "return",
  },
  factor_code_sha256: hash,
  snapshot_input_sha256: hash,
  result_sha256: hash,
  market_sha256: hash,
  program_sha256: hash,
  decisions: [
    {
      event_seq: 0,
      observation: {
        session_date: "2024-01-02",
        decision_time: "2024-01-02T21:00:00Z",
        warmup: false,
        next_open_time: "2024-01-03T14:30:00Z",
        snapshot_id: "snapshot-a",
        state,
        history: [],
        listings: [],
        account: {
          as_of: "2024-01-02T21:00:00Z",
          cash: 1000,
          equity: 1000,
          currency: "USD",
        },
      },
      observation_sha256: hash,
      program_sha256: hash,
      snapshot_id: "snapshot-a",
      reads: [
        {
          factor_listing_id: "factor-listing",
          native_listing_id: "native-listing",
          snapshot_id: "snapshot-a",
          factor_version_id: "version-a",
          row_sha256: hash,
          value: 0.12,
          availability: "available",
        },
      ],
      eligible: ["native-listing"],
      ranking: [{ listing_id: "native-listing", value: 0.12 }],
      targets: [{ listing_id: "native-listing", value: 0.5 }],
      eligibility_sha256: hash,
      score_sha256: hash,
      allocation_sha256: hash,
      reply_sha256: hash,
      reply: {
        next_state: state,
        decision: {
          action: "rebalance",
          basis: "target_weight",
          effective_at: "2024-01-03T14:30:00Z",
          omitted_positions: "liquidate",
          signal_id: "test-signal",
        },
      },
    },
  ],
  orders: [
    {
      native_order_id: "order-a",
      logical_order_id: hash,
      decision_sequence: 0,
      listing_id: "native-listing",
      side: "buy",
      quantity: 10,
      filled_quantity: 0,
      status: "open",
      commission: 0,
    },
  ],
  fills: [],
};
const render = (view: StructuredExecution) =>
  renderToStaticMarkup(
    createElement(StructuredExecutionPanel, {
      execution: view,
      side: "Before",
    }),
  );
it("withholds every derived value and flag while either side still needs release", () => {
  const html = renderToStaticMarkup(
    createElement(StructuredComparisonPanel, {
      display: {
        state: "withheld",
        before: "execution-a",
        after: "execution-b",
        pending: false,
      },
      release: () => {
        throw Error("render must never release");
      },
    }),
  );
  expect(html).toContain("Release before execution execution-a");
  expect(html).toContain("Release after execution execution-b");
  expect(html).not.toContain("Observed difference");
  expect(html).not.toContain("No observed change");
  expect(html).not.toContain("0.12");
});
it("unavailable capability does not render release buttons or results", () => {
  const html = renderToStaticMarkup(
    createElement(StructuredComparisonPanel, {
      display: { state: "unavailable", reason: "schema unavailable" },
      release: () => {
        throw Error("never");
      },
    }),
  );
  expect(html).toContain("schema unavailable");
  expect(html).not.toContain("<button");
  expect(html).not.toContain("Actual factor row values");
});
it("links exact factor and snapshot identities without inventing lineage", () => {
  const html = render(execution);
  expect(html).toContain("/factors/factor-a/versions/version-a");
  expect(html).toContain("/engine-runs/source-run-a/snapshots/snapshot-a");
  expect(html).toContain("No recorded parent factor version");
  expect(
    render({ ...execution, parent_factor_version_id: "parent-version" }),
  ).toContain("/factors/factor-a/versions/parent-version");
});
it("actual targets and unfilled orders never become synthetic fills", () => {
  const html = render(execution);
  expect(html).toContain('aria-label="Target allocations, not fills"');
  expect(html).toContain("filled 0");
  expect(html).toContain(
    "No observed fills. Targets and orders do not imply fills.",
  );
  expect(html).toContain("#structured-execution-a-decision-0");
  expect(html).toContain("#structured-execution-a-order-order-a");
});
it("observed fills link to exact native order and logical identity", () => {
  const html = render({
    ...execution,
    fills: [
      {
        native_fill_id: "fill-a",
        native_order_id: "order-a",
        logical_order_id: hash,
        listing_id: "native-listing",
        side: "buy",
        quantity: 3,
        price: 100,
        commission: 1,
        session_date: "2024-01-03",
      },
    ],
  });
  expect(html).toContain("#structured-execution-a-fill-fill-a");
  expect(html).toContain("Back to observed order order-a");
  expect(html).toContain("quantity 3; price 100");
});
it("shows actual no-change separately from target and economic changes", () => {
  const comparison: StructuredComparison = {
    schema_version: "mineagent.structured-pair-comparison.v1",
    before: execution,
    after: {
      ...execution,
      execution_id: "execution-b",
      factor_version_id: "version-b",
    },
    values: [],
    eligibility_changed: false,
    score_values_changed: true,
    ranking_changed: false,
    targets_changed: true,
    observed_orders_changed: false,
    observed_fills_changed: false,
  };
  const html = renderToStaticMarkup(
    createElement(StructuredComparisonPanel, {
      display: { state: "released", comparison },
      release: () => {
        throw Error("never");
      },
    }),
  );
  expect(html).toContain("Score values</dt><dd>Observed difference");
  expect(html).toContain("Observed fill economics</dt><dd>No observed change");
  expect(html).toContain("not a claim of financial improvement");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});
it("preserves configured SUBPATH and exact current deep-link fragments", async () => {
  vi.resetModules();
  vi.stubGlobal("document", {
    querySelector: () => ({ content: "/research" }),
  });
  vi.stubGlobal("window", {
    location: {
      pathname: "/research/engine-runs/run-a/structured-comparison",
      search: "?before=a&after=b",
    },
  });
  const { StructuredExecutionPanel: Prefixed } =
    await import("../../src/features/structured-comparison");
  const html = renderToStaticMarkup(
    createElement(Prefixed, { execution, side: "Before" }),
  );
  expect(html).toContain(
    'href="/research/factors/factor-a/versions/version-a"',
  );
  expect(html).toContain(
    'href="/research/engine-runs/source-run-a/snapshots/snapshot-a"',
  );
  expect(html).toContain(
    "/research/engine-runs/run-a/structured-comparison?before=a&amp;after=b#structured-execution-a-decision-0",
  );
});
