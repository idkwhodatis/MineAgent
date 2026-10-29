import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { normalOEUsageOptions } from "../../src/features/normal-oe-factor-usage";

afterEach(() => vi.unstubAllGlobals());
it.each([false, true])(
  "finishes one body across detach/remount (headers already received: %s)",
  async (headersReceived) => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    let finish!: (value: Response) => void;
    let signal!: AbortSignal;
    const fetcher = vi.fn((_url: string, init: RequestInit) => {
      signal = init.signal!;
      if (headersReceived) {
        return Promise.resolve(
          new Response(
            new ReadableStream<Uint8Array>({
              start(controller) {
                finish = () => {
                  controller.enqueue(
                    new TextEncoder().encode(JSON.stringify({ data: [] })),
                  );
                  controller.close();
                };
              },
            }),
          ),
        );
      }
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    });
    vi.stubGlobal("fetch", fetcher);
    const options = normalOEUsageOptions("factor", "version", 0);
    const first = new QueryObserver(client, options);
    const unsubscribe = first.subscribe(() => {});
    expect(fetcher).toHaveBeenCalledOnce();
    await Promise.resolve(); // Headers alone must not complete the query.
    expect(first.getCurrentResult().isPending).toBe(true);
    unsubscribe();
    expect(signal.aborted).toBe(false);
    const second = new QueryObserver(client, options);
    const detach = second.subscribe(() => {});
    expect(fetcher).toHaveBeenCalledOnce();
    finish(new Response(JSON.stringify({ data: [] })));
    await vi.waitFor(() =>
      expect(second.getCurrentResult().isSuccess).toBe(true),
    );
    expect(second.getCurrentResult().data).toEqual({ data: [] });
    detach();
    client.clear();
  },
);

it("rejects an old-session response rather than publishing it under a new owner", async () => {
  let token = "old";
  vi.stubGlobal("sessionStorage", {
    getItem: () =>
      JSON.stringify({ accessToken: token, expiresAt: Date.now() + 60000 }),
  });
  let finish!: (value: Response) => void;
  vi.stubGlobal(
    "fetch",
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const options = normalOEUsageOptions("factor", "version", 0);
  const pending = client.fetchQuery(options);
  const rejected = expect(pending).rejects.toMatchObject({
    code: "factor_read_session_unauthorized",
  });
  token = "new";
  finish(new Response(JSON.stringify({ data: [] })));
  await rejected;
  expect(client.getQueryData(options.queryKey)).toBeUndefined();
  client.clear();
});

it("preserves real transport failures instead of returning an empty usage list", async () => {
  vi.stubGlobal("fetch", () => Promise.reject(new Error("transport failed")));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await expect(
    client.fetchQuery(normalOEUsageOptions("factor", "version", 0)),
  ).rejects.toThrow("transport failed");
  client.clear();
});
