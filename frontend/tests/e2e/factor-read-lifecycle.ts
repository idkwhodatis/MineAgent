import type { Page, Request } from "@playwright/test";

/** A browser context closing does not prove an exposure transaction finished. */
export function trackFactorReads(page: Page) {
  const pending = new Map<Request, Promise<void>>();
  const finish = new Map<Request, () => void>();
  const onRequest = (request: Request) => {
    const path = new URL(request.url()).pathname;
    if (
      request.method() !== "GET" ||
      !/^\/api\/v1\/(factors|factor-snapshots|factor-comparisons|factor-packages)(\/|$)/.test(
        path,
      )
    )
      return;
    pending.set(
      request,
      new Promise<void>((resolve) => finish.set(request, resolve)),
    );
  };
  const onFinished = (request: Request) => {
    finish.get(request)?.();
    pending.delete(request);
    finish.delete(request);
  };
  let draining = false;
  let interrupted = false;
  let failed = 0;
  let closed = false;
  const onClosed = () => {
    closed = true;
  };
  const diagnostic = (reason: "deadline" | "interrupted" | "page_closed") =>
    `FACTOR_READ_LIFECYCLE_V1:reason=${reason};pending=${Math.min(pending.size, 64)};failed=${Math.min(failed, 64)}`;
  const onFailed = (request: Request) => {
    if (pending.has(request)) {
      interrupted = true;
      failed++;
    }
    onFinished(request);
  };
  page.on("request", onRequest);
  page.on("requestfinished", onFinished);
  page.on("requestfailed", onFailed);
  page.on("close", onClosed);
  const wait = async () => {
    if (draining) throw new Error("Overlapping factor read checkpoint");
    if (closed) throw new Error(diagnostic("page_closed"));
    draining = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        (async () => {
          while (pending.size) await Promise.all([...pending.values()]);
          if (closed) throw new Error(diagnostic("page_closed"));
          if (interrupted)
            throw new Error(
              `Factor exposure response interrupted: ${diagnostic(closed ? "page_closed" : "interrupted")}`,
            );
        })(),
        new Promise<never>((_, reject) => {
          timer = setTimeout(
            () =>
              reject(
                new Error(
                  `Factor exposure response incomplete: ${diagnostic(closed ? "page_closed" : "deadline")}`,
                ),
              ),
            15000,
          );
        }),
      ]);
    } finally {
      clearTimeout(timer);
      draining = false;
    }
  };
  const finishReads = async () => {
    try {
      await wait();
    } finally {
      page.off("request", onRequest);
      page.off("requestfinished", onFinished);
      page.off("requestfailed", onFailed);
      page.off("close", onClosed);
    }
  };
  return Object.assign(finishReads, { wait });
}
