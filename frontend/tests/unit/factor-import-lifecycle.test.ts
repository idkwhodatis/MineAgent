import type { ReactElement } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

// Exercise actual component callbacks with explicit hook/query doubles. This
// does not substitute for the real PostgreSQL/parser browser acceptance.
const hooks = vi.hoisted(() => ({
  values: [] as unknown[],
  cursor: 0,
  effects: [] as (() => void)[],
  admission: undefined as Record<string, unknown> | undefined,
  invalidate: vi.fn(async () => undefined),
}));
vi.mock("react", async (original) => ({
  ...(await original<typeof import("react")>()),
  useState: (initial: unknown) => {
    const index = hooks.cursor++;
    if (!(index in hooks.values)) hooks.values[index] = initial;
    return [
      hooks.values[index],
      (value: unknown) => {
        hooks.values[index] = value;
      },
    ];
  },
  useRef: (current: unknown) => {
    const index = hooks.cursor++;
    if (!(index in hooks.values)) hooks.values[index] = { current };
    return hooks.values[index];
  },
  useEffect: (effect: () => void) => {
    hooks.effects.push(effect);
  },
}));
vi.mock("@tanstack/react-query", async (original) => ({
  ...(await original<typeof import("@tanstack/react-query")>()),
  useQueryClient: () => ({
    isFetching: () => 0,
    getQueryCache: () => ({ findAll: () => [] }),
    invalidateQueries: hooks.invalidate,
  }),
  useIsFetching: () => 0,
  useQuery: () => ({ data: hooks.admission, isPending: false }),
}));
vi.mock("@tanstack/react-router", () => ({ Link: "a" }));
import { FactorImportPanel } from "../../src/features/factor-workbench/import";

type Node = ReactElement<Record<string, any>>;
function find(node: unknown, test: (node: Node) => boolean): Node | undefined {
  if (!node || typeof node !== "object") return undefined;
  if (Array.isArray(node))
    return node.map((item) => find(item, test)).find(Boolean);
  const element = node as Node;
  if (!element.props) return undefined;
  return test(element) ? element : find(element.props.children, test);
}
const onActiveChange = vi.fn(),
  onAdmission = vi.fn();
const id = "30000000-0000-4000-8000-000000000001";
let admissionId: string | undefined;
function render() {
  hooks.cursor = 0;
  hooks.effects = [];
  return FactorImportPanel({
    enabled: true,
    owner: "owner",
    admissionId,
    onActiveChange,
    onAdmission,
  });
}
function input() {
  return find(
    render(),
    (node) => node.type === "input" && node.props.id === "factor-import-file",
  )!;
}
function button(label: string) {
  return find(render(), (node) => node.props.children === label)!;
}
const archive = new File(["opaque archive fixture"], "factor.zip", {
  type: "application/zip",
});
function selectArchive() {
  input().props.onChange({ target: { files: [archive] } });
}

beforeEach(() => {
  hooks.values = [];
  hooks.admission = undefined;
  hooks.invalidate.mockClear();
  onActiveChange.mockClear();
  onAdmission.mockClear();
  admissionId = undefined;
  const storage = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  });
});
afterEach(() => vi.unstubAllGlobals());

it("resumes factor reads after acknowledged admission and clears only the selected upload", async () => {
  const sha = Buffer.from(
    await crypto.subtle.digest("SHA-256", await archive.arrayBuffer()),
  ).toString("hex");
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({ id, archive_sha256: sha, status: "queued" }),
        ),
    ),
  );
  selectArchive();
  expect(onActiveChange).toHaveBeenLastCalledWith(true);
  button("Import exact package").props.onClick();
  await vi.waitFor(() => expect(onAdmission).toHaveBeenCalledWith(id));
  expect(onActiveChange).toHaveBeenLastCalledWith(false);
  expect(button("Import exact package").props.disabled).toBe(true);
});

it("retains selected bytes and retry identity after an uncertain response", async () => {
  const fetcher = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
  vi.stubGlobal("fetch", fetcher);
  selectArchive();
  button("Import exact package").props.onClick();
  await vi.waitFor(() =>
    expect(button("Import exact package").props.disabled).toBe(false),
  );
  expect(onAdmission).not.toHaveBeenCalled();
  expect(onActiveChange).toHaveBeenLastCalledWith(true);
  button("Import exact package").props.onClick();
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect(fetcher.mock.calls[1][1].body).toBe(archive);
  expect(fetcher.mock.calls[1][1].headers["Idempotency-Key"]).toBe(
    fetcher.mock.calls[0][1].headers["Idempotency-Key"],
  );
});

it("lets a user abandon a selected archive without submitting or blocking the library", () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  selectArchive();
  const clear = button("Clear selected package");
  expect(clear).toBeDefined();
  clear.props.onClick();
  expect(onActiveChange).toHaveBeenLastCalledWith(false);
  expect(button("Import exact package").props.disabled).toBe(true);
  expect(fetcher).not.toHaveBeenCalled();
});

it.each(["queued", "processing", "failed", "blocked", "succeeded"])(
  "refreshes the saved-factor list only for an exact succeeded admission: %s",
  async (status) => {
    admissionId = id;
    hooks.admission = {
      id,
      status,
      local_bindings: {
        factor_id: "factor",
        version_id: "version",
        snapshots: [],
      },
    };
    const statusNode = find(render(), (node) => node.props.id === id)!;
    hooks.values = [];
    hooks.cursor = 0;
    hooks.effects = [];
    (statusNode.type as (props: unknown) => Node)(statusNode.props);
    hooks.effects.forEach((effect) => effect());
    if (status === "succeeded")
      await vi.waitFor(() =>
        expect(hooks.invalidate).toHaveBeenCalledWith(
          { queryKey: ["factors", "list"] },
          { cancelRefetch: false },
        ),
      );
    else expect(hooks.invalidate).not.toHaveBeenCalled();
  },
);
