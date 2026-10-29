import { createHash } from "node:crypto";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import {
  externalInputBytes,
  retainExternalInput,
} from "../../src/lib/research-input-intake";
import { RawExternalIntake } from "../../src/features/raw-external-intake";
vi.mock("../../src/lib/session", () => ({
  readSession: () => ({
    accessToken: "test-only",
    expiresAt: Date.now() + 10000,
  }),
  clearSession: vi.fn(),
}));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});
const id = "30000000-0000-4000-8000-000000000001";
it("sends exact UTF-8 bytes without JSON wrapping and verifies the retained digest", async () => {
  const text = "not JSON: 中文 😀";
  const sha = createHash("sha256").update(text).digest("hex");
  const call = vi.fn(async (_url: string, options: RequestInit) => {
    expect(await (options.body as Blob).text()).toBe(text);
    expect(options.headers).toMatchObject({
      "Content-Type": "application/octet-stream",
      Authorization: "Bearer test-only",
    });
    return new Response(
      JSON.stringify({
        id,
        sha256: sha,
        size_bytes: Buffer.byteLength(text),
        qualification: "raw_retained_not_normalized",
        workflow_created: false,
      }),
    );
  });
  vi.stubGlobal("fetch", call);
  expect((await retainExternalInput(text)).sha256).toBe(sha);
  expect(call.mock.calls[0][0]).toBe(
    "/api/v1/research-inputs/external-objects",
  );
});
it("bounds UTF-8 bytes, allows empty evidence and never truncates or sends oversized data", async () => {
  expect(externalInputBytes("").byteLength).toBe(0);
  expect(externalInputBytes("a".repeat(65536)).byteLength).toBe(65536);
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  await expect(retainExternalInput("😀".repeat(16385))).rejects.toThrow(
    "will not be truncated",
  );
  expect(fetcher).not.toHaveBeenCalled();
});
it("withholds a mismatched or falsely normalized receipt", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            id,
            sha256: "a".repeat(64),
            size_bytes: 3,
            qualification: "raw_retained_not_normalized",
            workflow_created: false,
          }),
        ),
    ),
  );
  await expect(retainExternalInput("abc")).rejects.toThrow(
    "differs from the exact supplied bytes",
  );
});
it("is disabled by default and never claims that retaining raw input runs a workflow", () => {
  const html = renderToStaticMarkup(
    createElement(RawExternalIntake, {
      reason: "schema_unavailable",
      onRetained: () => {
        throw new Error("Rendering must not publish");
      },
    }),
  );
  expect(html).toContain("Intake unavailable: schema_unavailable");
  expect(html).toContain('type="button" disabled=""');
  expect(html).toContain("No URL is fetched");
  expect(html).not.toContain("Retained raw input receipt");
});
it("preserves deployment SUBPATH for the authenticated intake request", async () => {
  vi.resetModules();
  vi.stubGlobal("document", {
    querySelector: () => ({ content: "/research" }),
  });
  const { retainExternalInput: prefixed } =
    await import("../../src/lib/research-input-intake");
  const fetcher = vi.fn(
    async (_url: string) =>
      new Response(
        JSON.stringify({
          id,
          sha256: createHash("sha256").update("").digest("hex"),
          size_bytes: 0,
          qualification: "raw_retained_not_normalized",
          workflow_created: false,
        }),
      ),
  );
  vi.stubGlobal("fetch", fetcher);
  await prefixed("");
  expect(fetcher.mock.calls[0]?.[0]).toBe(
    "/research/api/v1/research-inputs/external-objects",
  );
});
