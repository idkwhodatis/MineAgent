import { afterEach, describe, expect, it, vi } from "vitest";
import { engineHistorySearch } from "../../src/lib/navigation";
import {
  executionSnapshotOptions,
  engineCapabilitiesOptions,
  engineDetailOptions,
  engineHistoryOptions,
} from "../../src/lib/engine-runs-api";
import { runDetailRoute } from "../../src/lib/navigation";
import { QueryClient } from "@tanstack/react-query";

// HTTP interception here is a unit contract, not real-stack browser acceptance.
afterEach(() => vi.unstubAllGlobals());
describe("engine history identity and navigation", () => {
  it("never classifies an unknown schema as legacy seed", () => {
    expect(
      runDetailRoute({ schema_version: "mineagent.factor-research.v1" }),
    ).toBe("/engine-runs/$runId");
    expect(runDetailRoute({ schema_version: "unknown" })).toBe(
      "/engine-runs/$runId",
    );
    expect(runDetailRoute({ engine: "unknown" })).toBe("/engine-runs/$runId");
    expect(runDetailRoute(null)).toBe("/engine-runs/$runId");
    expect(runDetailRoute({ task: "cn_time" })).toBe("/runs/$runId");
  });
  it("retains bounded pagination in a reloadable URL", () => {
    expect(engineHistorySearch({ offset: "25" })).toEqual({ offset: 25 });
    for (const offset of [-1, 0.5, Infinity, "bad", 1000001])
      expect(engineHistorySearch({ offset })).toEqual({ offset: 0 });
  });
  it("keeps owner/family errors rather than substituting a legacy request", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: "not_found",
          message: "Supported engine history not found",
        }),
        { status: 404 },
      ),
    );
    vi.stubGlobal("fetch", fetcher);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    await expect(
      client.fetchQuery(engineDetailOptions("unknown")),
    ).rejects.toMatchObject({ status: 404, code: "not_found" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/engine-runs/unknown");
    client.clear();
  });
  it("uses the dedicated execution snapshot endpoint, never legacy factor snapshots", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "snapshot",
          values_available: false,
          analysis_results_available: false,
          qualification: "persisted_metadata_not_reexecuted_or_reverified",
        }),
      ),
    );
    vi.stubGlobal("fetch", fetcher);
    const client = new QueryClient();
    const metadata = await client.fetchQuery(
      executionSnapshotOptions("run", "snapshot"),
    );
    expect(metadata.values_available).toBe(false);
    expect(fetcher.mock.calls[0][0]).toBe(
      "/api/v1/engine-runs/run/execution-snapshots/snapshot",
    );
    client.clear();
  });
  it("reloads durable metadata via GET and exposes disabled capabilities", async () => {
    const fetcher = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            engines: [
              {
                engine: "rdagent",
                can_read_history: true,
                can_create: false,
              },
            ],
          }),
        ),
      ),
    );
    vi.stubGlobal("fetch", fetcher);
    const client = new QueryClient();
    const capabilities = await client.fetchQuery(engineCapabilitiesOptions);
    expect(capabilities.engines[0].can_create).toBe(false);
    await client.fetchQuery(engineHistoryOptions("saved", 25));
    client.clear();
    await client.fetchQuery(engineHistoryOptions("saved", 25));
    expect(fetcher.mock.calls.slice(1).map((call) => call[0])).toEqual([
      "/api/v1/engine-runs/saved/history?offset=25&limit=25",
      "/api/v1/engine-runs/saved/history?offset=25&limit=25",
    ]);
    for (const call of fetcher.mock.calls) expect(call[1].method).toBe("GET");
    client.clear();
  });
});
