import { expect, it } from "vitest";
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { refreshTerminalEngineEvidence } from "../../src/lib/engine-history-refresh";

it.each([false, true])(
  "reads final history and discards an older in-flight response (cached: %s)",
  async (cached) => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    const key = ["engine-history", "history", "run-a", 0];
    if (cached) client.setQueryData(key, "pending");
    let finishOld!: (value: string) => void;
    let calls = 0;
    const observer = new QueryObserver(client, {
      queryKey: key,
      queryFn: async () => {
        calls++;
        if (calls === 1)
          return new Promise<string>((resolve) => {
            finishOld = resolve;
          });
        return "completed snapshot";
      },
    });
    const unsubscribe = observer.subscribe(() => {});
    if (cached) void observer.refetch();
    expect(calls).toBe(1);
    await refreshTerminalEngineEvidence(client, "run-a");
    expect(calls).toBe(2);
    expect(observer.getCurrentResult().data).toBe("completed snapshot");
    finishOld("obsolete pending snapshot");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(observer.getCurrentResult().data).toBe("completed snapshot");
    unsubscribe();
    client.clear();
  },
);

it("invalidates all pages and final evidence without touching another run or eagerly reading inactive evidence", async () => {
  const client = new QueryClient();
  const related = [
    ["engine-history", "history", "run-a", 0],
    ["engine-history", "history", "run-a", 25],
    ["engine-history", "execution-snapshot", "run-a", "snapshot"],
    ["engine-history", "values", "run-a", "snapshot", 100],
    ["engine-history", "analysis", "run-a", "analysis"],
    ["engine-inputs", "run-a", 50],
  ];
  const untouched = [
    ["engine-history", "history", "run-b", 0],
    ["engine-inputs", "run-b", 0],
    ["engine-history", "detail", "run-a"],
    ["engine-history", "list", 0],
    ["engine-history", "capabilities"],
  ];
  for (const key of [...related, ...untouched]) client.setQueryData(key, {});
  await refreshTerminalEngineEvidence(client, "run-a");
  for (const key of related)
    expect(client.getQueryState(key)?.isInvalidated).toBe(true);
  for (const key of untouched)
    expect(client.getQueryState(key)?.isInvalidated).toBe(false);
  expect(client.isFetching()).toBe(0);
  client.clear();
});
