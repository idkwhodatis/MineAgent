import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  factorDetailOptions,
  factorRequest,
  factorSampleOptions,
  factorValuesOptions,
} from "../../src/lib/factor-api";
import {
  forgetFactorSubmission,
  submissionKey,
} from "../../src/features/factor-workbench/selection";
const storage = new Map<string, string>();
beforeEach(() => {
  storage.clear();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => storage.get(key) || null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  });
  vi.stubGlobal("window", { dispatchEvent: vi.fn() });
});
afterEach(() => vi.unstubAllGlobals());
describe("factor transport and request identity", () => {
  it("includes owner authentication only in headers and forwards cancellation", async () => {
    storage.set(
      "tradegrad.session.v1",
      JSON.stringify({
        accessToken: "private-token",
        expiresAt: Date.now() + 60000,
      }),
    );
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ id: "saved" }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetcher);
    const controller = new AbortController();
    await factorRequest("/factors", {
      signal: controller.signal,
      method: "POST",
      body: { name: "Saved" },
      key: "submission",
    });
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("/api/v1/factors");
    expect(init.headers).toEqual({
      Authorization: "Bearer private-token",
      "Content-Type": "application/json",
      "Idempotency-Key": "submission",
    });
    controller.abort();
    expect(init.signal.aborted).toBe(true);
  });
  it("uploads raw bounded archive bytes rather than multipart or JSON", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "admission", status: "queued" }), {
        status: 202,
      }),
    );
    vi.stubGlobal("fetch", fetcher);
    const body = new Blob(["opaque archive"]);
    await factorRequest("/factor-packages/import", {
      method: "POST",
      body,
      key: "exact-archive-key",
    });
    expect(fetcher.mock.calls[0][1].body).toBe(body);
    expect(fetcher.mock.calls[0][1].headers["Content-Type"]).toBe(
      "application/zip",
    );
    expect(fetcher.mock.calls[0][1].headers["Idempotency-Key"]).toBe(
      "exact-archive-key",
    );
  });
  it("keys queries by exact physical and definition identities", () => {
    expect(factorDetailOptions("factor", "v1").queryKey).not.toEqual(
      factorDetailOptions("factor", "v2").queryKey,
    );
    expect(factorValuesOptions("snapshot-a").queryKey).not.toEqual(
      factorValuesOptions("snapshot-b").queryKey,
    );
    expect(factorSampleOptions("s", "A", "2026-01-01").queryKey).not.toEqual(
      factorSampleOptions("s", "B", "2026-01-01").queryKey,
    );
  });
  it("discards explicitly cancelled query results without aborting the bounded HTTP body or replacing the newer cache", async () => {
    const cache = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    let oldSignal: AbortSignal | undefined;
    let finishOld: (() => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init: RequestInit) => {
        if (url.includes("snapshot-old")) {
          oldSignal = init.signal as AbortSignal;
          return new Promise<Response>((resolve) => {
            finishOld = () =>
              resolve(
                new Response(
                  JSON.stringify({
                    snapshot_id: "snapshot-old",
                    data: [],
                    count: 0,
                  }),
                ),
              );
          });
        }
        return Promise.resolve(
          new Response(
            JSON.stringify({ snapshot_id: "snapshot-new", data: [], count: 0 }),
          ),
        );
      }),
    );
    const old = cache
      .fetchQuery(factorValuesOptions("snapshot-old"))
      .catch(() => undefined);
    await cache.cancelQueries({
      queryKey: factorValuesOptions("snapshot-old").queryKey,
    });
    expect(oldSignal?.aborted).toBe(false);
    await cache.fetchQuery(factorValuesOptions("snapshot-new"));
    finishOld!();
    await old;
    await Promise.resolve();
    expect(
      cache.getQueryData(factorValuesOptions("snapshot-new").queryKey),
    ).toMatchObject({ snapshot_id: "snapshot-new" });
    expect(
      cache.getQueryData(factorValuesOptions("snapshot-old").queryKey),
    ).toBeUndefined();
    cache.clear();
  });
  it("preserves structured integrity errors instead of pretending no values", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: "factor_integrity",
            message: "Snapshot bytes differ",
            correlation_id: "ref",
          }),
          { status: 409 },
        ),
      ),
    );
    await expect(
      factorRequest("/factor-snapshots/s/values"),
    ).rejects.toMatchObject({
      code: "factor_integrity",
      message: "Snapshot bytes differ",
      status: 409,
    });
  });
  it("retains logical mutation key on response loss and reload, but never across owners or payload changes", () => {
    const first = submissionKey("owner-a", "create", { code: "x\n" });
    expect(submissionKey("owner-a", "create", { code: "x\n" })).toBe(first);
    expect(submissionKey("owner-b", "create", { code: "x\n" })).not.toBe(first);
    forgetFactorSubmission("owner-a", "create");
    expect(submissionKey("owner-a", "create", { code: "x\n" })).not.toBe(first);
    expect(submissionKey("owner-a", "create", { code: "x\r\n" })).not.toBe(
      first,
    );
  });
});
