import { expect, it } from "vitest";
import { assertStructuredWorkflowDeclaration } from "../../src/lib/workflow-structured";
import { workflowSubmissionPath } from "../../src/lib/research-workflows";
import type { StructuredWorkflowRequest } from "../../src/workflow-contracts/types.gen";
const request = () =>
  ({
    research: {
      window: { start: "2026-01-02", end: "2026-01-03" },
      budget: { calls: 2, storage_bytes: 100 },
    },
    structured: {
      objective: { window: { start: "2026-01-01", end: "2026-01-05" } },
      budget: { calls: 3, storage_bytes: 200 },
    },
    maximum_iterations: 2,
    budget_ceiling: { calls: 7, storage_bytes: 65936 },
  }) as unknown as StructuredWorkflowRequest;
it("one-day declaration and explicit combined ceiling are checked without mutation", () => {
  const value = request(),
    before = JSON.stringify(value);
  expect(() => assertStructuredWorkflowDeclaration(value)).not.toThrow();
  expect(JSON.stringify(value)).toBe(before);
  value.budget_ceiling.calls = 6;
  expect(() => assertStructuredWorkflowDeclaration(value)).toThrow("calls");
  expect(value.budget_ceiling.calls).toBe(6);
});
it("invalid dates, multi-day signals and no later execution fail closed", () => {
  const value = request();
  value.research.window.end = "2026-01-04";
  expect(() => assertStructuredWorkflowDeclaration(value)).toThrow(
    "one RD signal day",
  );
  value.research.window.start = "2026-02-30";
  expect(() => assertStructuredWorkflowDeclaration(value)).toThrow(
    "one RD signal day",
  );
  const same = request();
  same.structured.objective.window.end = "2026-01-03";
  expect(() => assertStructuredWorkflowDeclaration(same)).toThrow(
    "later execution",
  );
});
it("retry routing rejects mismatched or unknown version pairs", () => {
  expect(
    workflowSubmissionPath({
      schema_version: "mineagent.workflow-public-submit.v1",
      request: { schema_version: "mineagent.workflow-public-request.v1" },
    }),
  ).toBe("/research-workflows");
  expect(
    workflowSubmissionPath({
      schema_version: "mineagent.workflow-public-submit.v2",
      request: { schema_version: "mineagent.workflow-public-request.v2" },
    }),
  ).toBe("/research-workflows/structured");
  expect(() =>
    workflowSubmissionPath({
      schema_version: "mineagent.workflow-public-submit.v2",
      request: { schema_version: "mineagent.workflow-public-request.v1" },
    }),
  ).toThrow("version pair");
});
it("missing total budget fields cannot hide a declared child charge", () => {
  const value = request();
  value.structured.budget.tokens = 10;
  expect(() => assertStructuredWorkflowDeclaration(value)).toThrow("tokens");
});
