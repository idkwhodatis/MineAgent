import { expect, it, vi } from "vitest";
import {
  canAdvanceNormalOEUsage,
  normalOEUsageLink,
  normalOEUsagePath,
} from "../../src/features/normal-oe-factor-usage";
import type { StructuredExecutionRecord } from "../../src/client/types.gen";
const record: StructuredExecutionRecord = {
  execution_id: "execution",
  run_id: "run",
  factor_id: "factor",
  factor_version_id: "version",
  factor_version_ordinal: 1,
  parent_factor_version_id: null,
  execution_snapshot_id: "snapshot",
  structured_strategy_version_id: "program",
  qualification: "identity_metadata_not_result_release",
};
it("normal usage links target only normal search history, never legacy comparison or release", () => {
  expect(normalOEUsageLink(record, "factor", "version")).toBe(
    "/engine-runs/structured-search/runs/run",
  );
  expect(normalOEUsageLink(record, "foreign", "version")).toBeNull();
  expect(normalOEUsageLink(record, "factor", "foreign")).toBeNull();
  expect(
    normalOEUsageLink(
      { ...record, qualification: undefined },
      "factor",
      "version",
    ),
  ).toBeNull();
});
it("reverse query escapes actual factor/version IDs and preserves explicit pagination", () => {
  expect(normalOEUsagePath("f/a", "v?b", 50)).toBe(
    "/factors/f%2Fa/versions/v%3Fb/normal-oe-executions?offset=50",
  );
});
it("normal usage link preserves configured subpath", async () => {
  vi.resetModules();
  vi.stubGlobal("document", { querySelector: () => ({ content: "/mine" }) });
  try {
    const { normalOEUsageLink: link } =
      await import("../../src/features/normal-oe-factor-usage");
    expect(link(record, "factor", "version")).toBe(
      "/mine/engine-runs/structured-search/runs/run",
    );
  } finally {
    vi.unstubAllGlobals();
    vi.resetModules();
  }
});

it("pagination never advances past the server offset bound", () => {
  expect(canAdvanceNormalOEUsage(99_950, 50)).toBe(true);
  expect(canAdvanceNormalOEUsage(100_000, 50)).toBe(false);
  expect(canAdvanceNormalOEUsage(0, 49)).toBe(false);
});
