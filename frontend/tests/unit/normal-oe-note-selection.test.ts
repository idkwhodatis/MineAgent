import { expect, it } from "vitest";
import { safeNormalOENoteHref } from "../../src/lib/normal-oe-note-selection";
const id = "00000000-0000-0000-0000-000000000001";
const path = `/engine-runs/structured-search/runs/${id}`;
const params = () =>
  new URLSearchParams({
    normal_note_version: "1",
    execution_id: id,
    candidate_id: id,
    program_version_id: id,
    result_sha256: "a".repeat(64),
    evidence_sha256: "b".repeat(64),
    kind: "normal_oe_execution",
  });
it("accepts only complete normal versioned routes, not legacy or arbitrary destinations", () => {
  const value = `${path}?${params()}`;
  expect(safeNormalOENoteHref(value)).toBe(value);
  for (const invalid of [
    `https://evil.invalid${value}`,
    `//evil.invalid${value}`,
    `/strategies/${id}/research?${params()}`,
    value + "#else",
    value + "&extra=1",
    value + "&kind=normal_oe_fill",
  ])
    expect(safeNormalOENoteHref(invalid)).toBeNull();
});
it("decision and fill selection require their exact bounded kind-specific fields", () => {
  const decision = params();
  decision.set("kind", "normal_oe_decision");
  expect(safeNormalOENoteHref(`${path}?${decision}`)).toBeNull();
  decision.set("decision_sequence", "0");
  decision.set("row_sha256", "c".repeat(64));
  expect(safeNormalOENoteHref(`${path}?${decision}`)).not.toBeNull();
  decision.set("decision_sequence", "8");
  expect(safeNormalOENoteHref(`${path}?${decision}`)).toBeNull();
  const fill = params();
  fill.set("kind", "normal_oe_fill");
  fill.set("native_fill_id", "actual/fill?1");
  fill.set("listing_id", id);
  fill.set("session_date", "2026-10-28");
  fill.set("row_sha256", "c".repeat(64));
  expect(safeNormalOENoteHref(`${path}?${fill}`)).not.toBeNull();
  fill.set("session_date", "2026-02-30");
  expect(safeNormalOENoteHref(`${path}?${fill}`)).toBeNull();
});
it("missing hashes or cross-kind selectors never fall back to a run-only selection", () => {
  const bad = params();
  bad.delete("evidence_sha256");
  expect(safeNormalOENoteHref(`${path}?${bad}`)).toBeNull();
  const cross = params();
  cross.set("native_fill_id", "fill");
  expect(safeNormalOENoteHref(`${path}?${cross}`)).toBeNull();
});
it("draft selector requires separately admitted exact link plus matching record and released output", async () => {
  const { normalOENoteAnchor } =
    await import("../../src/lib/normal-oe-note-selection");
  const record = {
    execution_id: id,
    candidate_id: id,
    program_version_id: id,
    snapshot_id: id,
    source_run_id: id,
  } as import("../../src/client/types.gen").NormalExecutionRecord;
  const released = {
    candidate_id: id,
    execution: {
      run_id: id,
      execution_id: id,
      structured_strategy_version_id: id,
      result_sha256: "a".repeat(64),
      execution_snapshot_id: id,
      execution_snapshot_run_id: id,
      decisions: [{ event_seq: 0 }],
      fills: [],
    },
  } as unknown as import("../../src/client/types.gen").NormalExecutionView;
  const href = `${path}?${params()}`;
  expect(normalOENoteAnchor(href, null, record, released)).toBeNull();
  expect(normalOENoteAnchor(href, href, record, released)).toEqual({
    kind: "execution",
  });
  expect(
    normalOENoteAnchor(href, href, { ...record, execution_id: null }, released),
  ).toBeNull();
  const decision = params();
  decision.set("kind", "normal_oe_decision");
  decision.set("decision_sequence", "1");
  decision.set("row_sha256", "c".repeat(64));
  const absent = `${path}?${decision}`;
  expect(normalOENoteAnchor(absent, absent, record, released)).toBeNull();
  decision.set("decision_sequence", "0");
  const exact = `${path}?${decision}`;
  expect(normalOENoteAnchor(exact, exact, record, released)).toEqual({
    kind: "decision",
    sequence: 0,
  });
  expect(
    normalOENoteAnchor(exact, exact, record, {
      ...released,
      execution: {
        ...released.execution,
        decisions: [
          ...released.execution.decisions,
          ...released.execution.decisions,
        ],
      },
    }),
  ).toBeNull();
});
