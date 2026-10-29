import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import { OEProviderSelection } from "../../src/features/oe-provider-selection";
import type { StructuredSnapshotOption } from "../../src/client/types.gen";
const snapshot: StructuredSnapshotOption = {
  snapshot_id: "snapshot",
  factor_id: "factor",
  factor_version_id: "version",
  source_run_id: "source-run",
  dataset_version_id: "dataset",
  signal_window: { start: "2024-03-13", end: "2024-03-14" },
  snapshot_manifest_sha256: "a".repeat(64),
  release_receipt_id: "receipt",
};
const props = {
  snapshots: [snapshot],
  models: [],
  snapshotId: "snapshot",
  modelProfileId: "",
  onSnapshot: () => {},
  onModel: () => {},
};
it("shows release metadata and correct source-run navigation without implying eligibility", () => {
  const html = renderToStaticMarkup(createElement(OEProviderSelection, props));
  expect(html).toContain("/engine-runs/source-run/snapshots/snapshot");
  expect(html).toContain("/factors/factor/versions/version");
  expect(html).toContain("Fresh preflight is still required");
  expect(html).toContain("not an automatically chosen execution window");
  expect(html).toContain(
    "fixed retained synthetic or explicitly user-declared",
  );
  expect(html).toContain(
    "not independently verified live market or point-in-time",
  );
  expect(html).not.toContain("<button");
});
it("keeps missing selections visible instead of replacing them with the first catalog entry", () => {
  const html = renderToStaticMarkup(
    createElement(OEProviderSelection, {
      ...props,
      snapshotId: "old",
      modelProfileId: "old-model",
    }),
  );
  expect(html).toContain('value="old" selected=""');
  expect(html).toContain('value="old-model" selected=""');
  expect(html).toContain("it has not been replaced");
  expect(html).toContain("Do not substitute a model");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});
it("preserves SUBPATH for real source and factor links", async () => {
  vi.resetModules();
  vi.stubGlobal("document", {
    querySelector: () => ({ content: "/research" }),
  });
  const { OEProviderSelection: Prefixed } =
    await import("../../src/features/oe-provider-selection");
  const html = renderToStaticMarkup(createElement(Prefixed, props));
  expect(html).toContain("/research/engine-runs/source-run/snapshots/snapshot");
  expect(html).toContain("/research/factors/factor/versions/version");
});
