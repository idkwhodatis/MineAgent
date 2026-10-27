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
  const promise = drain.wait().then(done);
  page.emit("response", {});
  await Promise.resolve();
  expect(done).not.toHaveBeenCalled();
  page.emit("requestfinished", request);
  await promise;
  expect(done).toHaveBeenCalledOnce();
  expect(page.listenerCount("request")).toBe(1);
  // A checkpoint must not detach listeners: a later navigation's read is real.
  const later = read("/api/v1/factors/child/versions");
  page.emit("request", later);
  const finished = vi.fn();
  const teardown = drain().then(finished);
  page.emit("response", {});
  await Promise.resolve();
  expect(finished).not.toHaveBeenCalled();
  page.emit("requestfinished", later);
  await teardown;
  expect(finished).toHaveBeenCalledOnce();
  expect(page.listenerCount("request")).toBe(0);
});
it("never treats a cancelled pending exposure as a completed teardown", async () => {
  // Failures outside an active wait are equally uncertain; no silent deletion.
  for (const between of [false, true]) {
    const earlier = new EventEmitter();
    const earlierDrain = trackFactorReads(earlier as unknown as Page);
    if (between) await earlierDrain.wait();
    const cancelled = read();
    earlier.emit("request", cancelled);
    earlier.emit("requestfailed", cancelled);
    await expect(earlierDrain()).rejects.toThrow("reason=interrupted");
  }
  for (const pending of [false, true]) {
    const closed = new EventEmitter();
    const closedDrain = trackFactorReads(closed as unknown as Page);
    if (pending) closed.emit("request", read());
    closed.emit("close");
    await expect(closedDrain()).rejects.toThrow("reason=page_closed");
  }
  const page = new EventEmitter();
  const drain = trackFactorReads(page as unknown as Page);
  const request = read("/api/v1/factor-snapshots/owned/values");
  page.emit("request", request);
  const promise = drain();
  page.emit("requestfailed", request);
  await expect(promise).rejects.toThrow(
    "FACTOR_READ_LIFECYCLE_V1:reason=interrupted;pending=0;failed=1",
  );
});
it("bounds a genuinely unresolved read without retrying or cancelling it", async () => {
  vi.useFakeTimers();
  try {
    const page = new EventEmitter();
    const drain = trackFactorReads(page as unknown as Page);
    page.emit("request", read());
    const checked = expect(drain()).rejects.toThrow(
      "reason=deadline;pending=1;failed=0",
    );
    await vi.advanceTimersByTimeAsync(15000);
    await checked;
    expect(page.listenerCount("request")).toBe(0);
    const closed = new EventEmitter();
    const closedDrain = trackFactorReads(closed as unknown as Page);
    closed.emit("request", read());
    const closedCheck = expect(closedDrain()).rejects.toThrow(
      "reason=page_closed;pending=1;failed=0",
    );
    closed.emit("close");
    await vi.advanceTimersByTimeAsync(15000);
    await closedCheck;
    expect(closed.listenerCount("close")).toBe(0);
  } finally {
    vi.useRealTimers();
  }
});
