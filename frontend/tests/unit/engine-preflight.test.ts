import { afterEach, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import {
  engineDatasetsOptions,
  preflightEngineRun,
} from "../../src/lib/engine-runs-api";
import type { RdSubmissionRequest } from "../../src/client/types.gen";

afterEach(() => vi.unstubAllGlobals());
// Transport contracts only; no claimed real dataset or browser acceptance.
it("pages real dataset endpoint without treating unchecked datasets as eligible", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        data: [{ id: "owned", eligibility: "not_checked" }],
        count: 26,
      }),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient();
  const result = await client.fetchQuery(engineDatasetsOptions(25));
  expect(result.data[0].eligibility).toBe("not_checked");
  expect(fetcher.mock.calls[0][0]).toBe(
    "/api/v1/engine-runs/datasets?offset=25&limit=25",
  );
  expect(fetcher.mock.calls[0][1].method).toBe("GET");
  client.clear();
});
it("preflight preserves server eligibility and closed runtime without creating a run", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        data_eligible: true,
        can_submit: false,
        reasons: [
          { code: "runtime_disabled", message: "Execution not enabled" },
        ],
      }),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  const body = { dataset_version_id: "owned" } as RdSubmissionRequest;
  const result = await preflightEngineRun(body);
  expect(result.can_submit).toBe(false);
  expect(result.reasons[0].code).toBe("runtime_disabled");
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/engine-runs/preflight");
  expect(fetcher.mock.calls[0][1].body).toBe(JSON.stringify(body));
  expect(fetcher.mock.calls[0][1].headers["Idempotency-Key"]).toBeUndefined();
});
it("propagates owner and validation errors rather than inventing eligibility", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(
      new Response(
        JSON.stringify({ code: "not_found", message: "Dataset not found" }),
        { status: 404 },
      ),
    );
  vi.stubGlobal("fetch", fetcher);
  await expect(
    preflightEngineRun({} as RdSubmissionRequest),
  ).rejects.toMatchObject({ status: 404, code: "not_found" });
  expect(fetcher).toHaveBeenCalledTimes(1);
});
