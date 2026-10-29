/** Portable identities and withheld rendering; actual Chromium is a separate gate. */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";
const state = vi.hoisted(() => ({
  enabled: false,
  foreign: false,
  keys: [] as string[],
}));
vi.mock("@tanstack/react-query", () => ({
  queryOptions: (value: unknown) => value,
  useQuery: (options: { queryKey: unknown[] }) => {
    const key = options.queryKey.join(":");
    state.keys.push(key);
    return {
      refetch: vi.fn(),
      data: key.endsWith("capabilities")
        ? {
            can_read: state.enabled,
            can_create: false,
            reason_code: "retained_only",
          }
        : {
            data: [
              {
                execution_id: "execution",
                run_id: "run",
                factor_id: state.foreign ? "other" : "factor",
                factor_version_id: "version",
                qualification: "identity_metadata_not_result_release",
              },
            ],
          },
    };
  },
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: unknown }) => children,
  useParams: () => ({}),
  useSearch: () => ({}),
}));
vi.mock("../../src/lib/api", () => ({
  describeError: (error: Error) => ({ message: error.message }),
}));
import { FactorStructuredExecutions } from "../../src/features/structured-results";
import {
  structuredPairSearch,
  structuredReleaseRequired,
  structuredComparisonOptions,
} from "../../src/lib/structured-results-api";
beforeEach(() => {
  state.enabled = false;
  state.foreign = false;
  state.keys = [];
});
const render = () =>
  renderToStaticMarkup(
    createElement(FactorStructuredExecutions, {
      factorId: "factor",
      versionId: "version",
    }),
  );
it("unavailable schema never requests metadata or presents execution actions", () => {
  const html = render();
  expect(state.keys).toEqual(["structured-results:capabilities"]);
  expect(html).toContain("Structured history unavailable");
  expect(html).not.toContain("Open observed strategy execution");
  expect(html).not.toContain("Inspect normal OE run");
  expect(html).not.toContain("Next normal OE usage");
});
it("metadata discovery never discloses values or grants release", () => {
  state.enabled = true;
  const html = render();
  expect(html).toContain("Open observed strategy execution execution");
  expect(state.keys).toContain("factor-normal-oe-usage:factor:version:0");
  expect(html).toContain('href="/engine-runs/structured-search/runs/run"');
  expect(html).toContain("Inspect normal OE run run");
  expect(html).not.toContain("Observed difference");
  expect(html).not.toContain("Release before execution");
});
it("foreign exact version is not linked or substituted", () => {
  state.enabled = true;
  state.foreign = true;
  const html = render();
  expect(html).toContain("Execution identity differs");
  expect(html).not.toContain("Open observed strategy execution");
  expect(html).not.toContain("Inspect normal OE run");
});
it("invalid comparison IDs are rejected rather than defaulting to any seed", () => {
  expect(
    structuredPairSearch({ before: "not-an-id", after: [], seed: "anything" }),
  ).toEqual({ before: undefined, after: undefined });
  const id = "12345678-1234-1234-1234-123456789012";
  expect(structuredPairSearch({ before: id, after: id })).toEqual({
    before: id,
    after: id,
  });
  expect(
    structuredComparisonOptions("run", "before", "after").queryKey,
  ).toEqual(["structured-results", "pair", "run", "before", "after"]);
});
it("only explicit release-required errors offer release, not authentication or integrity failures", () => {
  expect(
    structuredReleaseRequired({ code: "engine_result_release_required" }),
  ).toBe(true);
  for (const code of [
    "not_found",
    "structured_result_inconsistent",
    "unauthorized",
  ])
    expect(structuredReleaseRequired({ code })).toBe(false);
});
