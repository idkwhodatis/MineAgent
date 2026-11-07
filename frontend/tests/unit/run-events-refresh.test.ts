import { beforeEach, expect, it, vi } from "vitest";
import {
  QueryClient,
  QueryObserver,
  type QueryFilters,
} from "@tanstack/react-query";

const hooks = vi.hoisted(() => ({
  effects: [] as (() => void)[],
  options: undefined as Record<string, unknown> | undefined,
  cancel: vi.fn<(filters: QueryFilters) => Promise<void>>(
    async () => undefined,
  ),
  invalidate: vi.fn<(filters: QueryFilters) => Promise<void>>(
    async () => undefined,
  ),
}));
vi.mock("react", () => ({
  useEffect: (effect: () => void) => hooks.effects.push(effect),
}));
vi.mock("@tanstack/react-query", async (original) => ({
  ...(await original<typeof import("@tanstack/react-query")>()),
  useQuery: (options: Record<string, unknown>) => {
    hooks.options = options;
    return {};
  },
}));
vi.mock("@/lib/api", () => ({
  queryClient: {
    cancelQueries: hooks.cancel,
    invalidateQueries: hooks.invalidate,
  },
}));
import { useRunEvents } from "../../src/lib/use-run-events";

beforeEach(() => {
  hooks.effects = [];
  hooks.cancel.mockReset().mockResolvedValue(undefined);
  hooks.invalidate.mockReset().mockResolvedValue(undefined);
});

it("fetches a final event batch when a consumer stops polling", async () => {
  useRunEvents("run-a", false);
  for (const effect of hooks.effects) effect();
  expect(hooks.cancel).not.toHaveBeenCalled();
  hooks.effects = [];
  useRunEvents("run-a", true);
  for (const effect of hooks.effects) effect();
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(hooks.options?.refetchInterval).toBe(false);
  expect(hooks.cancel).toHaveBeenCalledWith({
    queryKey: ["events", "run-a"],
    exact: true,
  });
  expect(hooks.invalidate).toHaveBeenCalledWith({
    queryKey: ["events", "run-a"],
    exact: true,
  });
});

it.each([false, true])(
  "replaces a pre-terminal request even without an earlier cached batch (%s)",
  async (cached) => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    const key = ["events", "run-a"];
    if (cached) client.setQueryData(key, ["started"]);
    let calls = 0;
    let finishOld!: (value: string[]) => void;
    const observer = new QueryObserver(client, {
      queryKey: key,
      queryFn: async () => {
        calls++;
        if (calls === 1)
          return new Promise<string[]>((resolve) => {
            finishOld = resolve;
          });
        return ["started", "completed"];
      },
    });
    const unsubscribe = observer.subscribe(() => {});
    if (cached) void observer.refetch();
    client.setQueryData(["events", "run-b"], ["other"]);
    hooks.cancel.mockImplementation((filters) => client.cancelQueries(filters));
    hooks.invalidate.mockImplementation((filters) =>
      client.invalidateQueries(filters),
    );
    useRunEvents("run-a", true);
    for (const effect of hooks.effects) effect();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(calls).toBe(2);
    expect(observer.getCurrentResult().data).toEqual(["started", "completed"]);
    finishOld(["started"]);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(observer.getCurrentResult().data).toEqual(["started", "completed"]);
    expect(client.getQueryState(["events", "run-b"])?.isInvalidated).toBe(
      false,
    );
    unsubscribe();
    client.clear();
  },
);
