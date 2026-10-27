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
  const onFailed = (request: Request) => {
    if (draining && pending.has(request)) interrupted = true;
    onFinished(request);
  };
  page.on("request", onRequest);
  page.on("requestfinished", onFinished);
  page.on("requestfailed", onFailed);
  return async () => {
    draining = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        (async () => {
          while (pending.size) await Promise.all([...pending.values()]);
          if (interrupted)
            throw new Error(
              "Factor exposure response interrupted during teardown",
            );
        })(),
        new Promise<never>((_, reject) => {
          timer = setTimeout(
            () =>
              reject(
                new Error(
                  "Factor exposure response incomplete before teardown",
                ),
              ),
            15000,
          );
        }),
      ]);
    } finally {
      clearTimeout(timer);
      page.off("request", onRequest);
      page.off("requestfinished", onFinished);
      page.off("requestfailed", onFailed);
    }
  };
}
