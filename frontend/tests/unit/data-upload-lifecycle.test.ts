import type { ReactElement } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const hooks = vi.hoisted(() => ({
  cleanups: [] as (() => void)[],
  states: 0,
  upload: vi.fn(),
  navigate: vi.fn(),
}));
vi.mock("react", async (original) => ({
  ...(await original<typeof import("react")>()),
  useRef: (current: unknown) => ({ current }),
  useState: (value: unknown) => [
    hooks.states++ === 0 ? new File(["csv data"], "data.csv") : value,
    vi.fn(),
  ],
  useEffect: (effect: () => () => void) => {
    hooks.cleanups.push(effect());
  },
}));
vi.mock("@tanstack/react-router", () => ({
  Link: "a",
  useNavigate: () => hooks.navigate,
}));
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: { id: "owner" } }),
}));
vi.mock("@/lib/api", () => ({ currentUserOptions: {} }));
vi.mock("@/client/sdk.gen", () => ({ uploadDataFile: hooks.upload }));
import { UploadDatasetPage } from "@/features/data-upload";

type Node = ReactElement<Record<string, any>>;
function findForm(node: unknown): Node | undefined {
  if (Array.isArray(node)) return node.map(findForm).find(Boolean);
  if (!node || typeof node !== "object") return undefined;
  const element = node as Node;
  if (!element.props) return undefined;
  return element.type === "form" ? element : findForm(element.props.children);
}
beforeEach(() => {
  vi.clearAllMocks();
  hooks.cleanups = [];
  hooks.states = 0;
  const storage = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  });
});
afterEach(() => vi.unstubAllGlobals());
it.each([false, true])(
  "upload completion navigates only while its view is still mounted (left=%s)",
  async (left) => {
    let complete!: (value: unknown) => void;
    hooks.upload.mockReturnValue(
      new Promise((resolve) => (complete = resolve)),
    );
    findForm(UploadDatasetPage())!.props.onSubmit({ preventDefault: vi.fn() });
    await vi.waitFor(() => expect(hooks.upload).toHaveBeenCalledOnce());
    if (left) hooks.cleanups.forEach((cleanup) => cleanup());
    complete({ data: { id: "accepted-import" } });
    await new Promise((resolve) => setTimeout(resolve, 0));
    if (left) expect(hooks.navigate).not.toHaveBeenCalled();
    else
      expect(hooks.navigate).toHaveBeenCalledWith({
        to: "/imports/$importId",
        params: { importId: "accepted-import" },
      });
  },
);
