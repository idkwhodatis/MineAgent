import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { OEProviderPreflightResult } from "../../src/features/oe-provider-preflight";
import type { ProviderStructuredPreflight } from "../../src/client/types.gen";
const budget = {
  calls: 1,
  tokens: 1,
  mutation_slots: 1,
  evaluator_executions: 2,
  wall_milliseconds: 1000,
  storage_bytes: 1024,
  process_slots: 4,
  cost_microunits: 0,
};
const binding = {
  profile_id: "model",
  profile_sha256: "b".repeat(64),
  model: "chat",
  max_output_tokens: 128,
  request_deadline_ms: 1000,
  synthetic: false,
};
const result: ProviderStructuredPreflight = {
  normalized_request: {
    snapshot_id: "snapshot",
    seed_parameters: {
      threshold: 0,
      top_k: 1,
      maximum_weight: 1,
      maximum_gross_weight: 1,
    },
    objective: {
      window: { start: "2024-03-13", end: "2024-03-15" },
      initial_cash: 100000,
    },
    budget,
    maximum_mutation_slots: 1,
    model_profile_id: "model",
    model_binding_sha256: "c".repeat(64),
  },
  request_sha256: "d".repeat(64),
  preflight_sha256: "e".repeat(64),
  fixed_input_sha256: "f".repeat(64),
  runtime_profile_sha256: "a".repeat(64),
  model_binding: binding,
  required_budget: budget,
  required_process_units: 4,
  data_eligible: true,
  can_submit: true,
  reasons: [],
  data_qualification: "synthetic_by_construction",
};
it("keeps synthetic data separate from operator model and never calls preflight a run", () => {
  const html = renderToStaticMarkup(
    createElement(OEProviderPreflightResult, {
      result,
      stale: false,
      onApplyBudget: () => {
        throw new Error("Render cannot change limits");
      },
    }),
  );
  expect(html).toContain("synthetic_by_construction");
  expect(html).toContain("operator transport");
  expect(html).toContain("A preflight creates no research run");
  expect(html).toContain("cumulative, not concurrent slots");
  expect(html).toContain("Applying changes the draft only");
});
it("stale preflight never claims current submit eligibility or permits budget application", () => {
  const html = renderToStaticMarkup(
    createElement(OEProviderPreflightResult, {
      result,
      stale: true,
      onApplyBudget: () => {},
    }),
  );
  expect(html).toContain("Fresh submission eligible: no");
  expect(html).toContain('type="button" disabled=""');
  expect(html).toContain("Declarations changed");
});
it("missing operator/budget is not replaced with a fabricated estimate", () => {
  const unavailable = {
    ...result,
    can_submit: false,
    model_binding: null,
    required_budget: null,
    reasons: [
      { code: "operator_unavailable", message: "Operator unavailable" },
    ],
  };
  const html = renderToStaticMarkup(
    createElement(OEProviderPreflightResult, {
      result: unavailable,
      stale: false,
      onApplyBudget: () => {},
    }),
  );
  expect(html).toContain("No required budget is available");
  expect(html).toContain("operator_unavailable");
  expect(html).not.toContain("Apply required budget to draft");
});
