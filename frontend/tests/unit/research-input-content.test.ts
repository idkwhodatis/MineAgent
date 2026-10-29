import { afterEach, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const id = "10000000-0000-4000-8000-000000000001";
afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});
async function fixture(raw: Uint8Array<ArrayBuffer>, prefix = "") {
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal("sessionStorage", {
    getItem: () =>
      JSON.stringify({ accessToken: "token", expiresAt: Date.now() + 60000 }),
  });
  vi.stubGlobal("document", { querySelector: () => ({ content: prefix }) });
  const sha = Array.from(
    new Uint8Array(await webcrypto.subtle.digest("SHA-256", raw)),
    (v) => v.toString(16).padStart(2, "0"),
  ).join("");
  const fetcher = vi
    .fn()
    .mockResolvedValue(
      new Response(raw, { headers: { "X-Content-SHA256": sha } }),
    );
  vi.stubGlobal("fetch", fetcher);
  const { readInputContent } =
    await import("../../src/lib/research-input-content");
  return { sha, fetcher, readInputContent };
}
it.each(["", "/mineagent"])(
  "explicit owner content fetch preserves %s prefix, bytes and commitment",
  async (prefix) => {
    const raw = new TextEncoder().encode("<script>passive input</script>");
    const h = await fixture(raw, prefix);
    const value = await h.readInputContent({ kind: "raw", id, sha256: h.sha });
    expect(value.text).toBe("<script>passive input</script>");
    expect(value.blob.type).toBe("application/octet-stream");
    expect(h.fetcher.mock.calls[0][0]).toBe(
      `${prefix}/api/v1/research-inputs/external-objects/${id}/content?expected_sha256=${h.sha}`,
    );
    expect(h.fetcher.mock.calls[0][1]).toMatchObject({
      cache: "no-store",
      headers: { Authorization: "Bearer token" },
    });
  },
);
it("bad UTF8 remains opaque downloadable bytes, never replacement text", async () => {
  const h = await fixture(new Uint8Array([255, 0]));
  const value = await h.readInputContent({ kind: "raw", id, sha256: h.sha });
  expect(value.text).toBeNull();
  expect(value.blob.size).toBe(2);
});
it("hash mismatch never exposes text", async () => {
  const h = await fixture(new TextEncoder().encode("secret"));
  await expect(
    h.readInputContent({ kind: "raw", id, sha256: "a".repeat(64) }),
  ).rejects.toThrow("commitment");
});
it("bounded stream rejects oversized normalized content rather than truncating", async () => {
  const h = await fixture(new Uint8Array(32769));
  await expect(
    h.readInputContent({ kind: "normalized", id, sha256: h.sha }),
  ).rejects.toThrow("bound");
});
it("session switch after fetch discards private content", async () => {
  const h = await fixture(new Uint8Array([97]));
  h.fetcher.mockImplementation(async () => {
    vi.stubGlobal("sessionStorage", { getItem: () => null });
    return new Response(new Uint8Array([97]), {
      headers: { "X-Content-SHA256": h.sha },
    });
  });
  await expect(
    h.readInputContent({ kind: "raw", id, sha256: h.sha }),
  ).rejects.toThrow("Session changed");
});
it("view/download starts closed without any fetch or raw automatic render", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const { InputContentActions } =
    await import("../../src/features/input-content");
  const html = renderToStaticMarkup(
    createElement(InputContentActions, {
      reference: { kind: "raw", id, sha256: "a".repeat(64) },
    }),
  );
  expect(html).toContain("View input");
  expect(html).toContain("Download retained input");
  expect(html).not.toContain("<pre>");
  expect(fetcher).not.toHaveBeenCalled();
});

it.each(["", "/mineagent"])(
  "feedback uses its source-qualified reader at %s",
  async (prefix) => {
    const raw = new TextEncoder().encode(
      '{"direction":"passive derived feedback"}',
    );
    const h = await fixture(raw, prefix);
    const value = await h.readInputContent({
      kind: "feedback",
      id,
      sha256: h.sha,
    });
    expect(h.fetcher.mock.calls[0][0]).toBe(
      `${prefix}/api/v1/research-inputs/feedback-versions/${id}/content?expected_sha256=${h.sha}`,
    );
    expect(value.text).toBe(new TextDecoder().decode(raw));
    expect(value.filename).toBe(`feedback-input-${id}.json`);
    expect(value.blob.type).toBe("application/json");
  },
);
it("feedback stream remains bounded and never falls back to normalized route", async () => {
  const h = await fixture(new Uint8Array(32769));
  await expect(
    h.readInputContent({ kind: "feedback", id, sha256: h.sha }),
  ).rejects.toThrow("bound");
  expect(h.fetcher).toHaveBeenCalledTimes(1);
});
