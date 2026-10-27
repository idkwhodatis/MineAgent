import { afterEach, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import {
  engineResultPath,
  releasedAnalysisOptions,
  releasedSnapshotValuesOptions,
  releaseEngineResult,
  exportExecutionSnapshot,
} from "../../src/lib/engine-runs-api";
afterEach(() => vi.unstubAllGlobals());
// Transport contracts, never a substitute for the genuine result browser gate.
it("a read requiring release never silently sends POST", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        code: "engine_result_release_required",
        message: "Explicit release required",
      }),
      { status: 409 },
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await expect(
    client.fetchQuery(releasedSnapshotValuesOptions("run", "snapshot")),
  ).rejects.toMatchObject({ code: "engine_result_release_required" });
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][1].method).toBe("GET");
  expect(fetcher.mock.calls[0][0]).toBe(
    "/api/v1/engine-runs/run/execution-snapshots/snapshot/values?offset=0&limit=100",
  );
  client.clear();
});
it("explicit release replay keeps the same typed resource URL and no caller proof", async () => {
  const receipt = {
    resource_kind: "direct_analysis",
    resource_id: "analysis",
    exposure_ids: ["factor", "label"],
  };
  const fetcher = vi
    .fn()
    .mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify(receipt))),
    );
  vi.stubGlobal("fetch", fetcher);
  expect(
    await releaseEngineResult("run", "analysis", "direct_analysis"),
  ).toEqual(receipt);
  expect(
    await releaseEngineResult("run", "analysis", "direct_analysis"),
  ).toEqual(receipt);
  for (const call of fetcher.mock.calls) {
    expect(call[0]).toBe("/api/v1/engine-runs/run/analyses/analysis/release");
    expect(call[1].method).toBe("POST");
    expect(call[1].body).toBeUndefined();
  }
});
it("analysis and export never coerce execution IDs into legacy snapshot endpoints", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(() => Promise.resolve(new Response("{}")));
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient();
  await client.fetchQuery(releasedAnalysisOptions("run", "analysis"));
  expect(await exportExecutionSnapshot("run", "snapshot")).toBeInstanceOf(Blob);
  expect(fetcher.mock.calls.map((c) => c[0])).toEqual([
    "/api/v1/engine-runs/run/analyses/analysis",
    "/api/v1/engine-runs/run/execution-snapshots/snapshot/export",
  ]);
  expect(
    engineResultPath("run/slash", "snapshot", "execution_snapshot"),
  ).toContain("run%2Fslash");
  client.clear();
});
