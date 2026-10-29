import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ReactElement } from "react";

const state = vi.hoisted(() => ({
  values: [] as unknown[],
  cursor: 0,
  quote: vi.fn(),
  model: {} as Record<string, unknown>,
}));
vi.mock("react", async (original) => ({
  ...(await original<typeof import("react")>()),
  useState: (initial: unknown) => {
    const index = state.cursor++;
    if (!(index in state.values))
      state.values[index] = typeof initial === "function" ? initial() : initial;
    return [
      state.values[index],
      (value: unknown) => {
        state.values[index] = value;
      },
    ];
  },
  useRef: (current: unknown) => ({ current }),
  useEffect: () => undefined,
}));
vi.mock("@tanstack/react-router", () => ({
  useSearch: () => ({}),
  useNavigate: () => vi.fn(),
  Link: "a",
}));
vi.mock("@tanstack/react-query", async (original) => ({
  ...(await original<typeof import("@tanstack/react-query")>()),
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => ({
    isPending: false,
    data:
      queryKey[0] !== "engine-history"
        ? { id: "owner", can_manage: false, data: [] }
        : queryKey[1] === "capabilities"
          ? {
              engines: [
                {
                  engine: "rdagent",
                  purpose: "factor_research",
                  can_preflight: true,
                  configured_model_can_create: true,
                  model_profiles: [state.model],
                },
              ],
            }
          : { data: [{ id: "dataset", name: "owned" }], count: 1 },
  }),
}));
vi.mock("../../src/lib/engine-runs-api", async (original) => ({
  ...(await original<typeof import("../../src/lib/engine-runs-api")>()),
  preflightEngineRun: state.quote,
}));
import { EnginePreflightPage } from "../../src/features/engine-preflight";

type Node = ReactElement<Record<string, any>>;
function render() {
  state.cursor = 0;
  const entry = EnginePreflightPage() as Node;
  return (entry.type as (props: unknown) => Node)(entry.props);
}
function find(
  node: unknown,
  predicate: (n: Node) => boolean,
): Node | undefined {
  if (!node || typeof node !== "object") return undefined;
  if (Array.isArray(node))
    return node.map((n) => find(n, predicate)).find(Boolean);
  const element = node as Node;
  if (!element.props) return undefined;
  return predicate(element) ? element : find(element.props.children, predicate);
}
function byId(id: string) {
  return find(render(), (n) => n.props.id === id)!;
}
function parent() {
  return find(
    render(),
    (n) => n.props.placeholder === "Owned factor-version UUID",
  )!;
}
function submitButton() {
  return find(
    render(),
    (n) => n.type === "button" && n.props.type === "submit",
  )!;
}
function createButton() {
  return find(
    render(),
    (n) => n.type === "button" && n.props.children === "Create RD-Agent run",
  )!;
}
const freshBudget = {
  wall_milliseconds: 556000,
  calls: 32,
  tokens: 4000,
  cost_microunits: 0,
  storage_bytes: 100000,
  process_slots: 36,
};
const seedBudget = { ...freshBudget, wall_milliseconds: 616000 };
beforeEach(() => {
  state.values = [];
  state.cursor = 0;
  state.quote.mockReset();
  state.model = {
    id: "model",
    can_create: true,
    seeded_can_create: true,
    recommended_budget: freshBudget,
    seeded_recommended_budget: seedBudget,
    reason_code: "available",
    seeded_reason_code: "available",
  };
  vi.stubGlobal("sessionStorage", { getItem: () => null });
  vi.stubGlobal(
    "FormData",
    class {
      get(key: string) {
        return (
          {
            acknowledged: "on",
            dataset: "dataset",
            start: "2024-01-01",
            end: "2024-01-05",
            lookback: "1",
            lag: "0",
            iterations: "1",
            name: "test",
            description: "task",
            formulation: "close",
            kind: "time_series",
            unit: "return",
            interpretation: "higher_preferred",
            horizon: "1",
            metric: "ic",
            direction: "at_least",
            threshold: "0",
            days: "1",
            pairs: "2",
          } as Record<string, string>
        )[key];
      }
      getAll() {
        return ["close"];
      }
    },
  );
  state.quote.mockImplementation(async (request) => ({
    normalized_request: request,
    can_submit: true,
    reasons: [],
    cutoff_count: 1,
    required_process_units: 36,
  }));
  byId("engine-generation-mode").props.onChange({
    target: { value: "configured" },
  });
  byId("engine-model-profile").props.onChange({ target: { value: "model" } });
});
afterEach(() => vi.unstubAllGlobals());
async function check() {
  await find(render(), (n) => n.type === "form")!.props.onSubmit({
    preventDefault() {},
    currentTarget: {},
  });
}
it("actual entry component sends fresh/seed-specific catalog budgets and invalidates parent quotes", async () => {
  await check();
  expect(state.quote.mock.lastCall![0].budget).toEqual(freshBudget);
  expect(state.quote.mock.lastCall![0].schema_version).toBe(
    "mineagent.rdagent-provider-submission.v1",
  );
  expect(createButton().props.disabled).toBe(false);
  for (const value of ["parent-a", "parent-b", ""]) {
    parent().props.onChange({ target: { value } });
    expect(createButton().props.disabled).toBe(true);
    await check();
    const request = state.quote.mock.lastCall![0];
    expect(request.budget).toEqual(value ? seedBudget : freshBudget);
    expect(request.parent_factor_version_id).toBe(value || undefined);
    expect(createButton().props.disabled).toBe(false);
  }
});
it("old or unavailable seeded catalog fails closed without borrowing fresh budget", async () => {
  delete state.model.seeded_recommended_budget;
  delete state.model.seeded_can_create;
  parent().props.onChange({ target: { value: "parent" } });
  expect(submitButton().props.disabled).toBe(true);
  await check();
  expect(state.quote).not.toHaveBeenCalled();
  parent().props.onChange({ target: { value: "" } });
  expect(submitButton().props.disabled).toBe(false);
  await check();
  expect(state.quote.mock.lastCall![0].budget).toEqual(freshBudget);
});
