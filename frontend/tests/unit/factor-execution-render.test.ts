/** Metadata presentation doubles only; actual HTTP/browser acceptance is separate. */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
const state = vi.hoisted(() => ({ data: [] as Record<string, unknown>[] }));
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: { data: state.data, count: state.data.length } }),
  queryOptions: (value: unknown) => value,
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: unknown }) => children,
}));
vi.mock("../../src/lib/api", () => ({
  describeError: (error: Error) => ({ message: error.message }),
}));
import { FactorExecutions } from "../../src/features/factor-workbench/executions";

const row = {
  kind: "execution_snapshot",
  factor_id: "factor",
  factor_version_id: "version",
  run_id: "run",
  execution_snapshot_id: "execution",
  source_id: "source",
  evaluation_id: "evaluation",
  publication_state: "completed",
  analysis_id: "analysis",
  analysis_state: "result_recorded",
  created_at: "2026-01-01T00:00:00Z",
  qualification: "identity_metadata_not_result_release",
};
const render = () =>
  renderToStaticMarkup(
    createElement(FactorExecutions, {
      factorId: "factor",
      versionId: "version",
    }),
  );
describe("Factor Library execution metadata", () => {
  it("links dedicated identities without implying metrics or release", () => {
    state.data = [row];
    const html = render();
    expect(html).toContain("Open execution snapshot execution");
    expect(html).toContain("/engine-runs/run/analyses/analysis");
    expect(html).toContain("measurement is not released here");
    expect(html).not.toContain("IC: 0");
    expect(html).not.toContain("/factor-snapshots/execution");
  });
  it("does not navigate a response for another immutable version", () => {
    state.data = [{ ...row, factor_version_id: "other" }];
    const html = render();
    expect(html).toContain("does not match");
    expect(html).not.toContain("Open execution snapshot");
    expect(html).not.toContain("/analyses/");
  });
  it("distinguishes no declared analysis and no retained records", () => {
    state.data = [
      { ...row, analysis_id: null, analysis_state: "not_declared" },
    ];
    expect(render()).toContain("No direct analysis declared");
    state.data = [];
    expect(render()).toContain("No calculation or analysis result is implied");
  });
});
