import { EventEmitter } from "node:events";
import type { Page, Request } from "@playwright/test";
import { expect, it, vi } from "vitest";
import { trackFactorReads } from "../e2e/factor-read-lifecycle";

function read(path = "/api/v1/factors") {
  return {
    url: () => `http://localhost${path}`,
    method: () => "GET",
  } as Request;
}
it("waits for the actual body completion, not response headers or visible controls", async () => {
  const page = new EventEmitter();
  const drain = trackFactorReads(page as unknown as Page);
  const request = read();
  page.emit("request", request);
  const done = vi.fn();
  const promise = drain().then(done);
  page.emit("response", {});
  await Promise.resolve();
  expect(done).not.toHaveBeenCalled();
  page.emit("requestfinished", request);
  await promise;
  expect(done).toHaveBeenCalledOnce();
  expect(page.listenerCount("request")).toBe(0);
});
it("never treats a cancelled pending exposure as a completed teardown", async () => {
  const page = new EventEmitter();
  const drain = trackFactorReads(page as unknown as Page);
  const request = read("/api/v1/factor-snapshots/owned/values");
  page.emit("request", request);
  const promise = drain();
  page.emit("requestfailed", request);
  await expect(promise).rejects.toThrow("interrupted");
});
it("bounds a genuinely unresolved read without retrying or cancelling it", async () => {
  vi.useFakeTimers();
  try {
    const page = new EventEmitter();
    const drain = trackFactorReads(page as unknown as Page);
    page.emit("request", read());
    const checked = expect(drain()).rejects.toThrow("incomplete");
    await vi.advanceTimersByTimeAsync(15000);
    await checked;
    expect(page.listenerCount("request")).toBe(0);
  } finally {
    vi.useRealTimers();
  }
});
