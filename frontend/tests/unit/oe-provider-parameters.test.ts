import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import {
  OEProviderParameters,
  oeBudgetFields,
} from "../../src/features/oe-provider-parameters";
import type { ProviderStructuredSearchRequest } from "../../src/client/types.gen";
const request: ProviderStructuredSearchRequest = {
  schema_version: "mineagent.oe-structured-provider-submission.v1",
  purpose: "strategy_search",
  engine: "openevolve",
  profile: "fixed_factor_operator_closed_program_v1",
  snapshot_id: "snapshot",
  model_profile_id: "operator",
  model_binding_sha256: "a".repeat(64),
  seed_parameters: {
    threshold: 0.2,
    top_k: 3,
    maximum_weight: 0.2,
    maximum_gross_weight: 0.6,
  },
  objective: {
    window: { start: "2024-03-13", end: "2024-03-15" },
    initial_cash: 100000,
    minimum_closed_trades: null,
  },
  budget: {
    calls: 0,
    tokens: 0,
    mutation_slots: 0,
    evaluator_executions: 0,
    wall_milliseconds: 0,
    storage_bytes: 0,
    process_slots: 0,
    cost_microunits: 0,
  },
  maximum_mutation_slots: 1,
};
it("shows four declared closed operators, objective and every explicit budget limit", () => {
  const before = JSON.stringify(request);
  const html = renderToStaticMarkup(
    createElement(OEProviderParameters, {
      request,
      onChange: () => {
        throw new Error("Rendering must not mutate");
      },
    }),
  );
  expect(html).toContain("not arbitrary Python");
  expect(html).toContain(
    "Actual orders, rejected orders and fills remain separate",
  );
  expect(html).toContain(
    "total return percentage minus maximum drawdown percentage",
  );
  expect(html).toContain("Billing remains unknown");
  for (const field of oeBudgetFields) expect(html).toContain(field);
  expect(html).not.toContain("Submit");
  expect(html).not.toContain("endpoint");
  expect(JSON.stringify(request)).toBe(before);
});
it("locks all declaration edits while an exact submitted envelope remains unresolved", () => {
  const html = renderToStaticMarkup(
    createElement(OEProviderParameters, {
      request,
      disabled: true,
      onChange: () => {},
    }),
  );
  expect(html).toMatch(/^<fieldset disabled="">/);
  expect(html).toContain("Zero limits do not authorize execution");
});
