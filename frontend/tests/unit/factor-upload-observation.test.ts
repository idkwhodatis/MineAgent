import { afterEach, expect, it, vi } from "vitest";
import {
  installFactorUploadObservation,
  takeFactorUploadObservation,
} from "../e2e/factor-upload-observation";
const url = "http://fixture.invalid/api/v1/factor-packages/import";
afterEach(() => {
  vi.unstubAllGlobals();
  delete (globalThis as { __factorRawUpload?: unknown }).__factorRawUpload;
});
function setup(maximum = 10_000_000) {
  const result = Promise.resolve(new Response("ok"));
  const native = vi.fn<typeof fetch>(() => result);
  vi.stubGlobal("fetch", native);
  vi.stubGlobal("location", { href: "http://fixture.invalid/factors" });
  installFactorUploadObservation({ url, key: "same-key", maximum });
  return { native, result };
}
it("observes exact binary File bytes while forwarding identical native fetch arguments and promise", async () => {
  const { native, result } = setup();
  const bytes = new Uint8Array([80, 75, 0, 255, 128, 1, 2]);
  const file = new File([bytes], "fixture.zip", { type: "application/zip" });
  const init = {
    method: "POST",
    headers: { "idempotency-key": "same-key" },
    body: file,
  };
  expect(fetch(url, init)).toBe(result);
  expect(native.mock.calls[0]).toEqual([url, init]);
  expect(native.mock.calls[0]?.[1]).toBe(init);
  const observation = await takeFactorUploadObservation();
  expect(observation).toEqual({
    keyMatches: true,
    size: bytes.length,
    bytes: [...bytes],
  });
  await expect(takeFactorUploadObservation()).rejects.toThrow("missing");
  // Each explicit retry is independently observed, without a synthetic request.
  fetch(url, init);
  expect((await takeFactorUploadObservation()).bytes).toEqual([...bytes]);
  expect(native).toHaveBeenCalledTimes(2);
});
it("rejects unsupported or oversized observations without blocking or changing the actual request", async () => {
  const { native, result } = setup(2);
  expect(
    fetch(url, { method: "POST", body: new File(["long"], "fixture.zip") }),
  ).toBe(result);
  await expect(takeFactorUploadObservation()).rejects.toThrow("bounded");
  fetch(url, { method: "POST", body: "not a File" });
  await expect(takeFactorUploadObservation()).rejects.toThrow("bounded");
  expect(native).toHaveBeenCalledTimes(2);
});
it("only observes the exact target POST and detects key drift or overlapping requests", async () => {
  const { native } = setup();
  fetch(url, { method: "GET" });
  fetch(`${url}-other`, { method: "POST", body: new File(["x"], "a.zip") });
  await expect(takeFactorUploadObservation()).rejects.toThrow("missing");
  const init = {
    method: "POST",
    headers: { "idempotency-key": "wrong" },
    body: new File(["x"], "a.zip"),
  };
  fetch(url, init);
  expect((await takeFactorUploadObservation()).keyMatches).toBe(false);
  fetch(url, init);
  fetch(url, init);
  await expect(takeFactorUploadObservation()).rejects.toThrow("overlapping");
  expect(native).toHaveBeenCalledTimes(5);
});
