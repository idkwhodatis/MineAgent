import { afterEach, expect, it, vi } from "vitest";
import type { ProviderStructuredSearchSubmit } from "../../src/client/types.gen";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});
function context(prefix = "") {
  vi.stubGlobal("document", { querySelector: () => ({ content: prefix }) });
  vi.stubGlobal("window", {
    location: { origin: "https://example.test" },
    dispatchEvent: vi.fn(),
  });
  vi.stubGlobal("sessionStorage", {
    getItem: () =>
      JSON.stringify({
        accessToken: "owned-token",
        expiresAt: Date.now() + 60000,
      }),
  });
}
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
it.each(["", "/lab/research"])(
  "generated normal SDK preserves auth, exact body/key and deployment %s",
  async (prefix) => {
    context(prefix);
    const fetcher = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(response({ id: "receipt" }, 201)),
      );
    vi.stubGlobal("fetch", fetcher);
    const { oeSubmit } = await import("../../src/lib/oe-provider-api");
    const body = {
      snapshot_id: "exact-snapshot",
      preflight_sha256: "a".repeat(64),
    } as ProviderStructuredSearchSubmit;
    await oeSubmit(body, "immutable-key");
    await oeSubmit(body, "immutable-key");
    for (const [request] of fetcher.mock.calls) {
      expect(request.url).toBe(
        `https://example.test${prefix}/api/v1/engine-runs/structured-search`,
      );
      expect(request.method).toBe("POST");
      expect(request.headers.get("Authorization")).toBe("Bearer owned-token");
      expect(request.headers.get("Idempotency-Key")).toBe("immutable-key");
      expect(await request.clone().json()).toEqual(body);
    }
  },
);
it("capability read performs no model, preflight or submission request", async () => {
  context();
  const fetcher = vi.fn().mockResolvedValue(
    response({
      routes_available: false,
      can_preflight: false,
      reason_code: "runtime_schema_unavailable",
      qualification: "metadata_not_submission_authority",
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  const { oeCapabilities } = await import("../../src/lib/oe-provider-api");
  expect((await oeCapabilities()).routes_available).toBe(false);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(
    fetcher.mock.calls[0][0].url.endsWith("/structured-search/capabilities"),
  ).toBe(true);
});
it("retains status and code needed to distinguish uncertain acceptance", async () => {
  context();
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        response({ code: "preflight_changed", message: "Recheck" }, 409),
      ),
  );
  const { oePreflight } = await import("../../src/lib/oe-provider-api");
  await expect(oePreflight({} as never)).rejects.toMatchObject({
    status: 409,
    code: "preflight_changed",
  });
});
it("discards a successful reply after the authenticated session changes", async () => {
  context();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() => {
      vi.stubGlobal("sessionStorage", { getItem: () => null });
      return Promise.resolve(response([]));
    }),
  );
  const { oeModels } = await import("../../src/lib/oe-provider-api");
  await expect(oeModels()).rejects.toThrow("Session changed");
});
it("only exact normal capability enables the page; generic model existence is not authority", async () => {
  const { normalSearchAvailable } =
    await import("../../src/features/oe-provider-page");
  expect(normalSearchAvailable()).toBe(false);
  expect(
    normalSearchAvailable({
      routes_available: true,
      can_preflight: true,
      qualification: "metadata_not_submission_authority",
      reason_code: null,
    }),
  ).toBe(true);
  for (const value of [
    { routes_available: true },
    { routes_available: true, can_preflight: false },
    { routes_available: true, can_preflight: true, qualification: "unknown" },
  ])
    expect(normalSearchAvailable(value as never)).toBe(false);
});
