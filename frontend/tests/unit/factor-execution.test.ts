import { describe, expect, it } from "vitest";
import { factorExecutionOptions } from "../../src/lib/factor-execution-api";

describe("dedicated execution navigation identity", () => {
  it("keeps factor, immutable version and page in separate query identity", () => {
    const first = factorExecutionOptions("factor", "version", 0).queryKey;
    expect(first).not.toEqual(
      factorExecutionOptions("other", "version", 0).queryKey,
    );
    expect(first).not.toEqual(
      factorExecutionOptions("factor", "other", 0).queryKey,
    );
    expect(first).not.toEqual(
      factorExecutionOptions("factor", "version", 20).queryKey,
    );
    expect(first).toContain("execution-records");
    expect(first).not.toContain("factor-snapshots");
  });
});

import { afterEach, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { exactFactorExecutionOptions } from "../../src/lib/factor-execution-api";
afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});
const record = {
  kind: "execution_snapshot",
  factor_id: "factor",
  factor_version_id: "version",
  execution_snapshot_id: "snapshot",
  run_id: "run",
};
it("resolves an exact execution after newer metadata pages without choosing latest", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          data: [{ ...record, execution_snapshot_id: "newer" }],
          count: 101,
        }),
      ),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ data: [record], count: 101 })),
    );
  vi.stubGlobal("fetch", fetcher);
  const result = await new QueryClient().fetchQuery({
    ...exactFactorExecutionOptions("factor", "version", "snapshot"),
    retry: false,
  });
  expect(result.execution_snapshot_id).toBe("snapshot");
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1][0]).toContain("offset=100&limit=100");
  expect(
    fetcher.mock.calls.every((call) => !String(call[0]).includes("/values")),
  ).toBe(true);
});
it("refuses a foreign version record even when snapshot ID matches", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          data: [{ ...record, factor_version_id: "foreign" }],
          count: 1,
        }),
      ),
    ),
  );
  await expect(
    new QueryClient().fetchQuery({
      ...exactFactorExecutionOptions("factor", "version", "snapshot"),
      retry: false,
    }),
  ).rejects.toThrow("exact factor version");
});
it("caps metadata discovery and never substitutes another result", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify({ data: [], count: 2000 }))),
    );
  vi.stubGlobal("fetch", fetcher);
  await expect(
    new QueryClient().fetchQuery({
      ...exactFactorExecutionOptions("factor", "version", "snapshot"),
      retry: false,
    }),
  ).rejects.toThrow("1,000-record");
  expect(fetcher).toHaveBeenCalledTimes(10);
});
it("preserves runtime SUBPATH for exact factor execution lookup", async () => {
  vi.resetModules();
  vi.stubGlobal("document", {
    querySelector: () => ({ content: "/research" }),
  });
  const fetcher = vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ data: [record], count: 1 })),
    );
  vi.stubGlobal("fetch", fetcher);
  const { exactFactorExecutionOptions: prefixed } =
    await import("../../src/lib/factor-execution-api");
  await new QueryClient().fetchQuery({
    ...prefixed("factor", "version", "snapshot"),
    retry: false,
  });
  expect(fetcher.mock.calls[0][0]).toBe(
    "/research/api/v1/factors/factor/versions/version/execution-records?offset=0&limit=100",
  );
});
