import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type {
  NormalExecutionRecord,
  NormalExecutionView,
} from "../../src/client/types.gen";
import {
  candidateLineage,
  OECandidateLineage,
  normalObservation,
  normalFillObservation,
} from "../../src/features/oe-candidate-lineage";

const row = (
  id: string,
  parent: string | null,
  ordinal = 0,
): NormalExecutionRecord => ({
  candidate_id: id,
  parent_candidate_id: parent,
  ordinal,
  origin: parent ? "model_proposal" : "authenticated_user_seed",
  program_version_id: `program-${id}`,
  execution_id: `execution-${id}`,
  snapshot_id: "snapshot",
  source_run_id: "source",
});
it("uses exact parent edges and retained ordinal rather than score ordering", () => {
  const graph = candidateLineage([
    row("child", "seed", 2),
    row("seed", null, 1),
    row("grandchild", "child", 3),
  ]);
  expect(graph.error).toBeNull();
  expect(
    graph.nodes.map((n) => [n.row.candidate_id, n.depth, n.missingParent]),
  ).toEqual([
    ["seed", 0, false],
    ["child", 1, false],
    ["grandchild", 2, false],
  ]);
});
it("distinguishes an unloaded parent from a root", () => {
  const graph = candidateLineage([row("child", "outside", 2)]);
  expect(graph.nodes[0].missingParent).toBe(true);
  const html = renderToStaticMarkup(
    createElement(OECandidateLineage, {
      records: [row("child", "outside", 2)],
      selected: "child",
      onSelect: () => {},
    }),
  );
  expect(html).toContain("Parent is not in the loaded records");
  expect(html).toContain('aria-pressed="true"');
  expect(html).toContain("not native Python checkpoints");
});
it.each([
  [row("a", null), row("a", null)],
  [row("a", "a")],
  [row("a", "b"), row("b", "a")],
  [row("a", "b"), row("b", "c"), row("c", "b")],
])(
  "rejects duplicate or cyclic identities without selectable nodes",
  (...records) => {
    const graph = candidateLineage(records);
    expect(graph.error).toBeTruthy();
    expect(graph.nodes).toEqual([]);
    const html = renderToStaticMarkup(
      createElement(OECandidateLineage, {
        records,
        selected: null,
        onSelect: () => {},
      }),
    );
    expect(html).toContain('role="alert"');
    expect(html).not.toContain("<button");
  },
);
it("bounds the graph without silently dropping candidates", () => {
  expect(
    candidateLineage(
      Array.from({ length: 2001 }, (_, i) => row(String(i), null, i)),
    ).error,
  ).toContain("bound");
});
it("renders metadata escaped and missing execution without an inspection button", () => {
  const r = row("<script>bad</script>", null);
  r.execution_id = null;
  const html = renderToStaticMarkup(
    createElement(OECandidateLineage, {
      records: [r],
      selected: null,
      onSelect: () => {},
    }),
  );
  expect(html).toContain("&lt;script&gt;");
  expect(html).not.toContain("<script>");
  expect(html).not.toContain("<button");
  expect(html).toContain("No execution recorded");
});
it("prepares only exact retained identities and hashes, not made-up metrics/assets", () => {
  const execution = {
    run_id: "run",
    execution_id: "execution",
    structured_strategy_version_id: "program",
    execution_snapshot_id: "snapshot",
    execution_snapshot_run_id: "source",
    factor_id: "factor",
    factor_version_id: "version",
    result_sha256: "result-hash",
    program_sha256: "program-hash",
    market_sha256: "market-hash",
    snapshot_input_sha256: "input-hash",
  };
  const result = normalObservation({
    candidate_id: "candidate",
    execution,
  } as NormalExecutionView);
  expect(result.kind).toBe("released_structured_oe_result");
  expect(result.refs).toEqual({
    candidate_id: "candidate",
    run_id: "run",
    execution_id: "execution",
    program_version_id: "program",
    snapshot_id: "snapshot",
    source_run_id: "source",
    factor_id: "factor",
    factor_version_id: "version",
    result_sha256: "result-hash",
    program_sha256: "program-hash",
    market_sha256: "market-hash",
    snapshot_input_sha256: "input-hash",
  });
  expect(result.sessions).toBeUndefined();
  expect(result.refs).not.toHaveProperty("assets");
});

it("keeps separate root branches visually separate even when ordinals interleave", () => {
  const graph = candidateLineage([
    row("a", null, 1),
    row("b", null, 2),
    row("a-child", "a", 3),
    row("b-child", "b", 4),
  ]);
  expect(graph.nodes.map((n) => n.row.candidate_id)).toEqual([
    "a",
    "a-child",
    "b",
    "b-child",
  ]);
});

it("binds selected assets and sessions only to actual released fills", () => {
  const value = {
    candidate_id: "candidate",
    execution: {
      run_id: "run",
      execution_id: "execution",
      structured_strategy_version_id: "program",
      execution_snapshot_id: "snapshot",
      execution_snapshot_run_id: "source",
      factor_id: "factor",
      factor_version_id: "version",
      result_sha256: "result",
      program_sha256: "program-hash",
      market_sha256: "market",
      snapshot_input_sha256: "input",
      fills: [
        {
          native_fill_id: "fill",
          listing_id: "retained-listing",
          session_date: "2024-03-15",
        },
      ],
    },
  } as NormalExecutionView;
  const context = normalFillObservation(value, "fill");
  expect(context.refs.listing_id).toBe("retained-listing");
  expect(context.refs.native_fill_id).toBe("fill");
  expect(context.refs.execution_id).toBe("execution");
  expect(context.sessions).toEqual(["2024-03-15"]);
  expect(() => normalFillObservation(value, "unknown")).toThrow(
    "not in this released execution",
  );
  expect(normalObservation(value).refs).not.toHaveProperty("listing_id");
});
