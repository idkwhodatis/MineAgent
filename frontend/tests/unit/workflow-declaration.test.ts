/** SSR callback wiring only; real browser interaction remains the028 gate. */
import { createElement, type ElementType } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
const capture = vi.hoisted(() => ({ changes: [] as Array<() => void> }));
vi.mock("react/jsx-runtime", async (original) => {
  const actual = await original<typeof import("react/jsx-runtime")>();
  return {
    ...actual,
    jsx: (type: ElementType, props: Record<string, unknown>, key?: string) => {
      if (type === "form" && typeof props.onChange === "function")
        capture.changes.push(props.onChange as () => void);
      return actual.jsx(type, props, key);
    },
    jsxs: (type: ElementType, props: Record<string, unknown>, key?: string) => {
      if (type === "form" && typeof props.onChange === "function")
        capture.changes.push(props.onChange as () => void);
      return actual.jsxs(type, props, key);
    },
  };
});
vi.mock("react/jsx-dev-runtime", async (original) => {
  const actual = await original<typeof import("react/jsx-dev-runtime")>();
  return {
    ...actual,
    jsxDEV: (...args: Parameters<typeof actual.jsxDEV>) => {
      const type = args[0];
      const props = args[1] as Record<string, unknown>;
      if (type === "form" && typeof props.onChange === "function")
        capture.changes.push(props.onChange as () => void);
      return actual.jsxDEV(...args);
    },
  };
});
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: unknown }) =>
    createElement("span", null, children as string),
  useNavigate: () => vi.fn(),
  useSearch: () => ({}),
}));
import { EngineEntryForm } from "../../src/features/engine-preflight";
import {
  engineCapabilitiesOptions,
  engineDatasetsOptions,
} from "../../src/lib/engine-runs-api";
it("every declaration form edit invalidates the parent workflow declaration, not only local RD preflight", () => {
  const client = new QueryClient();
  client.setQueryData(engineCapabilitiesOptions.queryKey, {
    engines: [
      {
        engine: "rdagent",
        purpose: "factor_research",
        can_preflight: true,
        model_profiles: [],
        configured_model_can_create: false,
        can_read_history: true,
        reason_code: "test",
      },
    ],
  });
  client.setQueryData(engineDatasetsOptions(0).queryKey, {
    data: [],
    count: 0,
  });
  const onDeclaration = vi.fn();
  renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client },
      createElement(EngineEntryForm, {
        ownerId: "owner",
        initialParent: "",
        workflowDeclaration: onDeclaration,
      }),
    ),
  );
  expect(capture.changes.length).toBeGreaterThan(0);
  capture.changes.at(-1)!();
  expect(onDeclaration).toHaveBeenCalledWith(null);
  client.clear();
});
