/** Portable rendering/identity tests, not real browser or persisted result proof. */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";
const state = vi.hoisted(() => ({
  queries: [] as string[],
  foreign: false,
  missingAnalysis: false,
}));
vi.mock("@tanstack/react-query", () => ({
  queryOptions: (x: unknown) => x,
  useQuery: (options: { queryKey: unknown[] }) => {
    const key = options.queryKey.join(":");
    state.queries.push(key);
    const refetch = vi.fn();
    if (key.startsWith("factor-execution-exact"))
      return {
        data: {
          kind: "execution_snapshot",
          factor_id: "factor",
          factor_version_id: "version",
          run_id: "run",
          execution_snapshot_id: "snapshot",
          evaluation_id: "evaluation",
          publication_state: "completed",
          analysis_id: state.missingAnalysis ? null : "analysis",
          analysis_state: state.missingAnalysis
            ? "not_declared"
            : "result_recorded",
        },
        refetch,
      };
    if (key.includes(":detail:"))
      return {
        data: {
          id: "run",
          task_name: "Declared close return",
          task_description: "Declared previous-window return",
        },
        refetch,
      };
    if (key.includes("execution-snapshot"))
      return {
        data: {
          id: "snapshot",
          run_id: "run",
          factor_version_id: state.foreign ? "other" : "version",
          evaluation_id: "evaluation",
          state: "completed",
          window_start: "2024-01-01",
          window_end: "2024-01-02",
          dataset_version_id: "dataset",
          input_sha256: "a".repeat(64),
          output_sha256: "b".repeat(64),
        },
        refetch,
      };
    return {
      error: {
        code: "engine_result_release_required",
        message: "Release required",
      },
      refetch,
    };
  },
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: unknown }) => children,
}));
vi.mock("../../src/lib/api", () => ({
  describeError: (error: Error) => ({ message: error.message }),
}));
import {
  ExecutionVersionResult,
  ExecutionTabContent,
} from "../../src/features/factor-workbench/execution-tabs";
import type { FactorVersion } from "../../src/lib/factor-api";
const version: FactorVersion = {
  id: "version",
  factor_id: "factor",
  ordinal: 1,
  parent_version_id: null,
  definition: {
    factor_kind: "time_series",
    input_fields: [{ name: "close" }],
    lookback_sessions: 2,
    lag_sessions: 0,
    output_unit: "return",
  },
  definition_sha256: "a".repeat(64),
  code: "exact retained source",
  code_sha256: "b".repeat(64),
  source_kind: "rdagent",
  source_provenance: {},
  created_at: "2024-01-01",
};
const render = (view: "values" | "evaluation") =>
  renderToStaticMarkup(
    createElement(ExecutionVersionResult, {
      version,
      snapshotId: "snapshot",
      view,
    }),
  );
beforeEach(() => {
  state.queries = [];
  state.foreign = false;
  state.missingAnalysis = false;
});
it("embeds actual typed values release in the factor page without leaking counts", () => {
  const html = render("values");
  expect(html).toContain("Lookback 2 prior sessions");
  expect(html).toContain("Declared close return");
  expect(html).toContain("not a verified symbolic formula");
  expect(html).toContain("2024-01-01 ≤ session &lt; 2024-01-02");
  expect(html).toContain("Release historical result");
  expect(html).toContain("not verified intermediate operands");
  expect(html).not.toContain("retained rows");
  expect(html).not.toContain("Mean IC");
  expect(state.queries.some((x) => x.includes("factor-snapshots"))).toBe(false);
});
it("evaluation uses the exact recorded analysis and still requires explicit release", () => {
  expect(render("evaluation")).toContain("Release historical result");
  expect(state.queries).toContain("engine-history:analysis:run:analysis");
  expect(render("evaluation")).not.toContain("Mean rank IC");
});
it("missing analysis is not mislabeled measured or successful", () => {
  state.missingAnalysis = true;
  expect(render("evaluation")).toContain(
    "No direct analysis declared for this exact execution",
  );
  expect(state.queries.some((x) => x.includes(":analysis:"))).toBe(false);
});
it("foreign metadata blocks any result/read/release panel", () => {
  state.foreign = true;
  const html = render("values");
  expect(html).toContain("does not match");
  expect(html).not.toContain("Release historical result");
  expect(state.queries.some((x) => x.includes(":values:"))).toBe(false);
});
it("mixed families never dispatch execution or legacy result queries", () => {
  const html = renderToStaticMarkup(
    createElement(ExecutionTabContent, {
      version,
      selection: {
        execution: "snapshot",
        snapshot: "legacy",
        invalidExecution: true,
      },
      versions: [],
      changeSelection: () => {
        throw Error("No navigation while rendering");
      },
    }),
  );
  expect(html).toContain("Malformed or mixed legacy/research");
  expect(state.queries).toEqual([]);
});
