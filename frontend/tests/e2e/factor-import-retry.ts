/** Acceptance-only bounded UI retry; busy is never a successful admission. */
export async function retryFactorBusy<
  T extends { status: number; code: unknown },
>(
  submit: (remainingMs: number) => Promise<T>,
  pause: (milliseconds: number) => Promise<void>,
  now: () => number = () => performance.now(),
): Promise<T> {
  const deadline = now() + 5000;
  for (let attempt = 0; ; attempt++) {
    const remaining = deadline - now();
    if (remaining <= 0) throw new Error("Exact import retry deadline exceeded");
    const result = await submit(remaining);
    if (now() > deadline)
      throw new Error("Exact import retry deadline exceeded");
    if (result.status !== 503 || result.code !== "factor_busy" || attempt === 3)
      return result;
    const delay = Math.min(100 * 2 ** attempt, deadline - now());
    if (delay <= 0) throw new Error("Exact import retry deadline exceeded");
    await pause(delay);
  }
}
