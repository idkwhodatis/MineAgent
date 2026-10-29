import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { InputDerivationAudit } from "../../src/features/input-derivation-audit";
import type { InputDerivationView } from "../../src/input-audit-contracts/types.gen";
const base: InputDerivationView = {
  derivation_id: "derivation",
  workflow_id: "workflow",
  stage_id: "stage",
  operation: "external_normalization",
  source_sha256: "a".repeat(64),
  raw_input_sha256: "b".repeat(64),
  mapping_sha256: "c".repeat(64),
  status: "started_no_terminal_receipt",
  error_code: null,
  prepared_at: "2026-10-26T22:00:00Z",
  started_at: "2026-10-26T22:00:01Z",
  terminal_at: null,
  outputs: [],
};
it("started without terminal stays uncertain and fabricates neither running nor ready output", () => {
  const html = renderToStaticMarkup(
    createElement(InputDerivationAudit, { value: base }),
  );
  expect(html).toContain("Start recorded; no terminal receipt");
  expect(html).toContain("does not prove that processing is still running");
  expect(html).toContain("No derived version references recorded");
  expect(html).toContain("format and bounds only");
  expect(html).not.toContain("<button");
});
it("shows exact output lineage without converting proposals into approval or selection", () => {
  const value: InputDerivationView = {
    ...base,
    status: "succeeded",
    terminal_at: "2026-10-26T22:00:02Z",
    outputs: [
      {
        origin: "normalized_external_proposal",
        record_id: "record",
        version_id: "version",
        content_sha256: "d".repeat(64),
      },
    ],
  };
  const render = (v: InputDerivationView) =>
    renderToStaticMarkup(createElement(InputDerivationAudit, { value: v }));
  const html = render(value);
  expect(html).toContain("Derivation completed");
  expect(html).toContain("Normalized external proposal");
  expect(html).toContain("Record record; version version");
  expect(html).toContain("not a selection, a submitted model request");
  expect(html).toContain(
    "Manual user guidance remains a separate pinned source",
  );
  expect(html).toBe(render(JSON.parse(JSON.stringify(value))));
  const feedback = render({
    ...value,
    operation: "experiment_feedback_projection",
    raw_object_id: null,
    raw_input_sha256: null,
    mapping_sha256: null,
    outputs: value.outputs!.map((ref) => ({
      ...ref,
      origin: "experiment_feedback_proposal",
    })),
  });
  expect(feedback).toContain("View feedback output");
  expect(feedback).not.toContain("View normalized output");
  expect(feedback).not.toContain("View input");
  expect(feedback).not.toContain("<pre");
});
it("failed derivation displays only safe error category and no fabricated output", () => {
  const value: InputDerivationView = {
    ...base,
    status: "failed",
    terminal_at: "2026-10-26T22:00:02Z",
    error_code: "invalid_json_or_mapping",
  };
  const html = renderToStaticMarkup(
    createElement(InputDerivationAudit, { value }),
  );
  expect(html).toContain("Recorded error category: invalid_json_or_mapping");
  expect(html).toContain(
    "Raw content and normalized text are private by default",
  );
  expect(html).not.toContain("<pre");
});
