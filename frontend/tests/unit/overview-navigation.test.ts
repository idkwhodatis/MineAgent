import { afterEach, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";

afterEach(() => vi.unstubAllGlobals());
it("empty overview has two real seed links but one unambiguous page-header entry", async () => {
  vi.stubGlobal("window", { location: { origin: "https://overview.test" } });
  vi.stubGlobal("sessionStorage", {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
  });
  const network = vi.fn(() => {
    throw new Error("SSR navigation proof must not call a backend");
  });
  vi.stubGlobal("fetch", network);
  const { OverviewPage } = await import("../../src/features/overview");
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  for (const key of [["runs", 0, 100], ["datasets"], ["strategies"]])
    query.setQueryData(key, { data: [], count: 0 });
  const router = createRouter({
    routeTree: createRootRoute({
      component: () => createElement(OverviewPage),
    }),
    history: createMemoryHistory({ initialEntries: ["/"] }),
    isServer: true,
  });
  await router.load();
  const html = renderToString(
    createElement(
      QueryClientProvider,
      { client: query },
      createElement(RouterProvider, { router }),
    ),
  );
  const seedLinks = (raw: string) =>
    [...raw.matchAll(/<a\b[^>]*href="\/runs\/new"[^>]*>[\s\S]*?<\/a>/g)].filter(
      ([link]) => link.includes("New seed evaluation"),
    );
  expect(seedLinks(html)).toHaveLength(2);
  const headers = [
    ...html.matchAll(
      /<header\b[^>]*class="page-header"[^>]*>[\s\S]*?<\/header>/g,
    ),
  ]
    .map(([header]) => header)
    .filter((header) => header.includes("<h1>Research overview</h1>"));
  expect(headers).toHaveLength(1);
  expect(seedLinks(headers[0])).toHaveLength(1);
  expect(network).not.toHaveBeenCalled();
  query.clear();
});
