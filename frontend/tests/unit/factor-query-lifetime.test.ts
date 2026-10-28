import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import {
  factorCapabilitiesOptions,
  factorValuesOptions,
} from "../../src/lib/factor-api";
import {
  factorExecutionOptions,
  exactFactorExecutionOptions,
} from "../../src/lib/factor-execution-api";
import {
  factorStructuredOptions,
  structuredCapabilityOptions,
  structuredRecordsOptions,
  structuredExecutionOptions,
  structuredComparisonOptions,
} from "../../src/lib/structured-results-api";
import { requireFactorReadsIdle } from "../../src/lib/factor-publication";

const structuredQueries = [
  factorStructuredOptions("factor", "version"),
  structuredCapabilityOptions,
  structuredRecordsOptions("run"),
  structuredExecutionOptions("run", "execution"),
  structuredComparisonOptions("run", "before", "after"),
];

afterEach(() => vi.unstubAllGlobals());
function harness() {
  let token: string | null = "owner-a";
  vi.stubGlobal("sessionStorage", {
    getItem: () =>
      token
        ? JSON.stringify({ accessToken: token, expiresAt: Date.now() + 60000 })
        : null,
    removeItem: () => {
      token = null;
    },
  });
  vi.stubGlobal("window", { dispatchEvent: vi.fn() });
  const requests: {
    signal: AbortSignal;
    body: (value: unknown, status?: number) => void;
  }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((_url, init: RequestInit) => {
      return new Promise<Response>((resolve, reject) => {
        init.signal!.addEventListener("abort", () =>
          reject(new DOMException("Timed out", "TimeoutError")),
        );
        requests.push({
          signal: init.signal as AbortSignal,
          body: (value, status = 200) =>
            resolve(new Response(JSON.stringify(value), { status })),
        });
      });
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  return {
    client,
    requests,
    setToken: (value: string | null) => {
      token = value;
    },
    token: () => token,
  };
}

it("finishes one bounded query/body across StrictMode-equivalent unsubscribe/remount and refetch joins", async () => {
  // Actual installed QueryObserver subscription lifecycle, not a browser claim.
  for (const options of [
    factorCapabilitiesOptions,
    factorValuesOptions("snapshot"),
    factorExecutionOptions("factor", "version"),
    ...structuredQueries,
  ]) {
    const { client, requests } = harness();
    const first = new QueryObserver(
      client,
      options as typeof factorCapabilitiesOptions,
    );
    const unsubscribe = first.subscribe(() => {});
    expect(requests).toHaveLength(1);
    unsubscribe();
    expect(requests[0].signal.aborted).toBe(false);
    expect(client.isFetching()).toBe(1);
    if (options.queryKey[1] === "values")
      expect(() => requireFactorReadsIdle(client)).toThrow("still in progress");
    const second = new QueryObserver(
      client,
      options as typeof factorCapabilitiesOptions,
    );
    const stop = second.subscribe(() => {});
    const joined = second.refetch({ cancelRefetch: false });
    const invalidated = client.invalidateQueries(
      { queryKey: options.queryKey },
      { cancelRefetch: false },
    );
    expect(requests).toHaveLength(1);
    requests[0].body({ enabled: true, data: [], count: 0 });
    await joined;
    await invalidated;
    expect(second.getCurrentResult().status).toBe("success");
    expect(client.isFetching()).toBe(0);
    stop();
    client.clear();
  }
});

it("does not publish a late old-owner response after query clear/logout or owner change", async () => {
  for (const clear of [false, true]) {
    const { client, requests, setToken } = harness();
    const result = client
      .fetchQuery(factorCapabilitiesOptions)
      .catch((error) => error);
    setToken(clear ? null : "owner-b");
    if (clear) client.clear();
    requests[0].body({ enabled: true, secret: "old-owner" });
    await result;
    await Promise.resolve();
    expect(
      client.getQueryData(factorCapabilitiesOptions.queryKey),
    ).toBeUndefined();
    expect(requests[0].signal.aborted).toBe(false);
    client.clear();
  }
  const { client, requests, setToken, token } = harness();
  const result = client
    .fetchQuery(factorCapabilitiesOptions)
    .catch((error) => error);
  setToken("owner-b");
  requests[0].body({ code: "unauthorized" }, 401);
  await result;
  expect(token()).toBe("owner-b"); // Old 401 cannot sign out a newer session.
  client.clear();
});

it("keeps the original twenty-second transport deadline and real errors", async () => {
  const timer = new AbortController();
  const timeout = vi
    .spyOn(AbortSignal, "timeout")
    .mockReturnValue(timer.signal);
  const { client, requests } = harness();
  try {
    const result = client.fetchQuery(factorCapabilitiesOptions);
    expect(timeout).toHaveBeenCalledWith(20000);
    const rejected = expect(result).rejects.toMatchObject({
      name: "TimeoutError",
    });
    timer.abort();
    await rejected;
    expect(requests).toHaveLength(1);
    expect(
      client.getQueryData(factorCapabilitiesOptions.queryKey),
    ).toBeUndefined();
  } finally {
    client.clear();
    timeout.mockRestore();
  }
});

it("checks the session around the entire bounded exact-execution pagination", async () => {
  const { client, requests, setToken } = harness();
  const result = client
    .fetchQuery(exactFactorExecutionOptions("factor", "version", "snapshot"))
    .catch((error) => error);
  requests[0].body({ data: [], count: 101 });
  await vi.waitFor(() => expect(requests).toHaveLength(2));
  setToken("owner-b");
  requests[1].body({
    data: [
      {
        kind: "execution_snapshot",
        factor_id: "factor",
        factor_version_id: "version",
        execution_snapshot_id: "snapshot",
      },
    ],
    count: 101,
  });
  expect(await result).toMatchObject({
    message: expect.stringContaining("session changed"),
  });
  expect(
    client.getQueryData(
      exactFactorExecutionOptions("factor", "version", "snapshot").queryKey,
    ),
  ).toBeUndefined();
  client.clear();
});

it("keeps obsolete-key body work visible until completion without replacing the selected key", async () => {
  const { client, requests } = harness();
  const observer = new QueryObserver(client, factorValuesOptions("old"));
  const stop = observer.subscribe(() => {});
  observer.setOptions(factorValuesOptions("new"));
  expect(requests).toHaveLength(2);
  expect(requests[0].signal.aborted).toBe(false);
  requests[1].body({ snapshot_id: "new", data: [], count: 0 });
  await vi.waitFor(() =>
    expect(observer.getCurrentResult().data).toMatchObject({
      snapshot_id: "new",
    }),
  );
  expect(() => requireFactorReadsIdle(client)).toThrow("still in progress");
  requests[0].body({ snapshot_id: "old", data: [], count: 0 });
  await vi.waitFor(() => expect(client.isFetching()).toBe(0));
  expect(observer.getCurrentResult().data).toMatchObject({
    snapshot_id: "new",
  });
  expect(() => requireFactorReadsIdle(client)).not.toThrow();
  stop();
  client.clear();
});

it("awaits the actual streamed JSON body after headers and observer unmount", async () => {
  const { client } = harness();
  let stream!: ReadableStreamDefaultController<Uint8Array>;
  const fetcher = vi.fn().mockResolvedValue(
    new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          stream = controller;
        },
      }),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  const observer = new QueryObserver(client, factorValuesOptions("streamed"));
  const stop = observer.subscribe(() => {});
  await Promise.resolve();
  stop();
  expect(client.isFetching()).toBe(1);
  expect(() => requireFactorReadsIdle(client)).toThrow("still in progress");
  stream.enqueue(
    new TextEncoder().encode('{"snapshot_id":"streamed","data":[],"count":0}'),
  );
  await Promise.resolve();
  expect(client.isFetching()).toBe(1); // Bytes without EOF are not completion.
  stream.close();
  await vi.waitFor(() => expect(client.isFetching()).toBe(0));
  expect(
    client.getQueryData(factorValuesOptions("streamed").queryKey),
  ).toMatchObject({ snapshot_id: "streamed" });
  expect(fetcher).toHaveBeenCalledOnce();
  client.clear();
});

it("refuses old-owner errors and next-page fetches under the actual production retry policy", async () => {
  const { queryClient: production } = await import("../../src/lib/api");
  const policy = production.getDefaultOptions().queries!;
  for (const status of [200, 409]) {
    const { client, requests, setToken } = harness();
    client.setDefaultOptions({
      queries: { ...policy, gcTime: Infinity, retryDelay: 0 },
    });
    const result = client
      .fetchQuery(exactFactorExecutionOptions("factor", "version", "snapshot"))
      .catch((error) => error);
    setToken("owner-b");
    requests[0].body(
      status === 200
        ? { data: [], count: 101 }
        : { message: "private old-owner error" },
      status,
    );
    expect(await result).toMatchObject({
      code: "factor_read_session_unauthorized",
    });
    expect(requests).toHaveLength(1);
    client.clear();
  }
});

it("waits for a pre-mutation body then refreshes once instead of accepting its stale revision", async () => {
  const { refreshFactorsAfterMutation } =
    await import("../../src/lib/factor-publication");
  const { client, requests } = harness();
  const observer = new QueryObserver(client, factorCapabilitiesOptions);
  const stop = observer.subscribe(() => {});
  const refreshed = refreshFactorsAfterMutation(client);
  await Promise.resolve();
  expect(requests).toHaveLength(1);
  expect(requests[0].signal.aborted).toBe(false);
  requests[0].body({ enabled: true, revision: 1 });
  await vi.waitFor(() => expect(requests).toHaveLength(2));
  requests[1].body({ enabled: true, revision: 2 });
  await refreshed;
  expect(observer.getCurrentResult().data).toMatchObject({ revision: 2 });
  expect(requests).toHaveLength(2);
  stop();
  client.clear();
});

it("rejects old-session structured results and errors for every exact query key", async () => {
  for (const options of structuredQueries) {
    for (const status of [200, 401, 409]) {
      const { client, requests, setToken, token } = harness();
      const result = client
        .fetchQuery(options as typeof structuredCapabilityOptions)
        .catch((error) => error);
      setToken("owner-b");
      requests[0].body({ secret: "old-owner" }, status);
      expect(await result).toMatchObject({
        code: "factor_read_session_unauthorized",
      });
      expect(client.getQueryData(options.queryKey)).toBeUndefined();
      expect(token()).toBe("owner-b");
      expect(requests).toHaveLength(1);
      client.clear();
    }
  }
});

it("retains structured transport deadlines and response failures without retries", async () => {
  for (const options of structuredQueries) {
    const timer = new AbortController();
    const timeout = vi
      .spyOn(AbortSignal, "timeout")
      .mockReturnValue(timer.signal);
    const { client, requests } = harness();
    try {
      const result = client.fetchQuery(
        options as typeof structuredCapabilityOptions,
      );
      expect(timeout).toHaveBeenCalledWith(20000);
      const rejected = expect(result).rejects.toMatchObject({
        name: "TimeoutError",
      });
      timer.abort();
      await rejected;
      expect(requests).toHaveLength(1);
      expect(client.getQueryData(options.queryKey)).toBeUndefined();
    } finally {
      timeout.mockRestore();
      client.clear();
    }
    const failed = harness();
    const result = failed.client.fetchQuery(
      options as typeof structuredCapabilityOptions,
    );
    failed.requests[0].body({ code: "release_required" }, 409);
    await expect(result).rejects.toMatchObject({
      status: 409,
      code: "release_required",
    });
    expect(failed.requests).toHaveLength(1);
    expect(failed.client.getQueryData(options.queryKey)).toBeUndefined();
    failed.client.clear();
  }
});
