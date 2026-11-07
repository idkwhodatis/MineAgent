import type { ReactElement } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type {
  DatasetPublic,
  RunCreate,
  StrategyPublic,
} from "@/client/types.gen";
const hooks = vi.hoisted(() => ({
  cleanup: undefined as (() => void) | undefined,
  create: vi.fn(),
  invalidate: vi.fn(),
  navigate: vi.fn(),
}));
vi.mock("react", async (original) => ({
  ...(await original<typeof import("react")>()),
  useRef: (current: unknown) => ({ current }),
  useState: (value: unknown) => [value, vi.fn()],
  useEffect: (effect: () => () => void) => {
    hooks.cleanup = effect();
  },
}));
vi.mock("@tanstack/react-router", () => ({
  Link: "a",
  useNavigate: () => hooks.navigate,
  useSearch: () => ({}),
}));
vi.mock("@/lib/api", () => ({
  createRun: hooks.create,
  queryClient: { invalidateQueries: hooks.invalidate },
  currentUserOptions: {},
  datasetsOptions: {},
  strategiesOptions: {},
}));
vi.mock("react-hook-form", () => ({
  useForm: ({ defaultValues }: { defaultValues: RunCreate }) => ({
    register: () => ({}),
    watch: (key: keyof RunCreate) => defaultValues[key],
    setValue: vi.fn(),
    setError: vi.fn(),
    handleSubmit: (submit: (body: RunCreate) => Promise<void>) => () =>
      submit(defaultValues),
    formState: { errors: {}, isSubmitting: false },
  }),
}));
import { RunForm } from "@/features/new-run";
import {
  loadSubmission,
  saveSubmission,
  submissionFor,
} from "@/lib/idempotency";
type Node = ReactElement<Record<string, any>>;
function findForm(node: unknown): Node | undefined {
  if (Array.isArray(node)) return node.map(findForm).find(Boolean);
  if (!node || typeof node !== "object") return undefined;
  const element = node as Node;
  if (!element.props) return undefined;
  return element.type === "form" ? element : findForm(element.props.children);
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function form() {
  return findForm(
    RunForm({
      owner: "owner",
      datasets: [
        {
          id: "dataset",
          market: "CN",
          source: "synthetic_fixture",
          manifest: { listings: ["one"] },
          start_date: "2024-01-01",
          end_date: "2024-02-01",
        } as unknown as DatasetPublic,
      ],
      strategies: [
        { id: "strategy", name: "seed", task: "cn_time" } as StrategyPublic,
      ],
    }),
  )!;
}
beforeEach(() => {
  vi.clearAllMocks();
  hooks.cleanup = undefined;
  hooks.create.mockResolvedValue({ id: "accepted" });
  hooks.invalidate.mockResolvedValue(undefined);
  const storage = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  });
});
afterEach(() => vi.unstubAllGlobals());
it("does not navigate after leaving while creation is in flight", async () => {
  const request = deferred<{ id: string }>();
  hooks.create.mockReturnValue(request.promise);
  const pending = form().props.onSubmit();
  hooks.cleanup!();
  request.resolve({ id: "old-run" });
  await pending;
  expect(hooks.navigate).not.toHaveBeenCalled();
});
it("rechecks navigation ownership after query invalidation", async () => {
  const refresh = deferred<void>();
  hooks.invalidate.mockReturnValue(refresh.promise);
  const pending = form().props.onSubmit();
  await vi.waitFor(() => expect(hooks.invalidate).toHaveBeenCalled());
  hooks.cleanup!();
  refresh.resolve();
  await pending;
  expect(hooks.navigate).not.toHaveBeenCalled();
});
it("preserves a newer submission written while an older request completes", async () => {
  const request = deferred<{ id: string }>();
  hooks.create.mockReturnValue(request.promise);
  const pending = form().props.onSubmit();
  const old = loadSubmission("owner")!;
  const newer = submissionFor({ ...old.body, end_date: "2024-01-30" });
  hooks.cleanup!();
  saveSubmission("owner", newer);
  request.resolve({ id: "old-run" });
  await pending;
  expect(loadSubmission("owner")).toEqual(newer);
});
it("clears its own accepted record and navigates while still mounted", async () => {
  await form().props.onSubmit();
  expect(loadSubmission("owner")).toBeNull();
  expect(hooks.navigate).toHaveBeenCalledWith({
    to: "/runs/$runId",
    params: { runId: "accepted" },
  });
});
it("still navigates when accepted record cleanup cannot access storage", async () => {
  const storage = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: () => {
      throw new Error("storage unavailable");
    },
  });
  await form().props.onSubmit();
  expect(hooks.navigate).toHaveBeenCalledTimes(1);
});
