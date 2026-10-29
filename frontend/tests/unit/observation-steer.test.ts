import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import {
  observationContext,
  observationSteerText,
  type ResearchObservation,
} from "../../src/lib/observation-steer";
import { ObservationSteer } from "../../src/features/observation-steer";
vi.mock("../../src/lib/session", () => ({
  readSession: () => ({ accessToken: "synthetic-owner" }),
  watchSession: () => () => {},
}));
const observation = {
  kind: "rd_analysis",
  refs: { snapshot_id: "snapshot", result_sha256: "a".repeat(64) },
  sessions: ["2024-03-16", "2024-03-15"],
};
it("retains exact references and selected sessions in deterministic reviewed text", () => {
  const context = observationContext(observation);
  expect(JSON.parse(context)).toEqual({
    kind: "rd_analysis",
    refs: { result_sha256: "a".repeat(64), snapshot_id: "snapshot" },
    sessions: ["2024-03-15", "2024-03-16"],
  });
  expect(observation.sessions).toEqual(["2024-03-16", "2024-03-15"]);
  expect(observationSteerText("  Study reversal  ", context)).toBe(
    `Study reversal\n\nUser-selected observation context (evidence pointers, not approval or enforced constraints):\n${context}`,
  );
});
it("does not infer sessions, assets, returns or applied guidance", () => {
  const context = observationContext({
    kind: "workflow_stage_metadata",
    refs: { stage_id: "stage" },
  });
  expect(JSON.parse(context)).toEqual({
    kind: "workflow_stage_metadata",
    refs: { stage_id: "stage" },
  });
  const html = renderToStaticMarkup(
    createElement(ObservationSteer, { observation }),
  );
  expect(html).toContain("Prepare guidance from this observation");
  expect(html).not.toContain("Saved version");
  expect(html).not.toContain("textarea");
});
it.each([
  { kind: "inject\ntext", refs: { stage_id: "stage" } },
  { kind: "valid", refs: {} },
  { kind: "valid", refs: { stage_id: "first\nsecond" } },
  { kind: "valid", refs: { stage_id: "x".repeat(129) } },
  { ...observation, sessions: ["2024-03-15", "2024-03-15"] },
  { ...observation, sessions: ["wrong"] },
  {
    ...observation,
    sessions: Array.from(
      { length: 33 },
      (_, i) => `2024-03-${String(i + 1).padStart(2, "0")}`,
    ),
  },
])(
  "rejects invalid or oversized context without truncating evidence",
  (value) => {
    expect(() => observationContext(value as ResearchObservation)).toThrow();
  },
);
it("requires authored guidance and enforces complete 2048-character text bound", () => {
  expect(() =>
    observationSteerText(" ", observationContext(observation)),
  ).toThrow();
  expect(() =>
    observationSteerText("x".repeat(2048), observationContext(observation)),
  ).toThrow("2048");
});
