import { afterEach, expect, it, vi } from "vitest";
import { normalizeSubpath } from "../../src/lib/deployment";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});
it("accepts root and canonical nested prefixes only", () => {
  for (const prefix of ["", "/", "/lab/research-7", "/api"])
    expect(normalizeSubpath(prefix)).toBe(prefix === "/" ? "" : prefix);
  for (const prefix of [
    "/ok\n/also-ok",
    "/ok\n",
    "/ok\r",
    "/a/",
    "//a",
    "/.",
    "/a/..",
    "/a//b",
    "/a%2fb",
    "/a?x",
    "/a#x",
    "/a\\b",
    "/é",
    "a",
    "/" + "a".repeat(200),
  ])
    expect(() => normalizeSubpath(prefix)).toThrow();
});
it.each(["", "/lab/research-7"])(
  "uses runtime prefix %s for API, links and session keys",
  async (prefix) => {
    vi.resetModules();
    vi.stubGlobal("document", { querySelector: () => ({ content: prefix }) });
    vi.stubGlobal("window", {
      location: {
        origin: "https://example.test",
        pathname: prefix + "/runs/123",
        search: "?tab=log",
      },
      dispatchEvent: vi.fn(),
    });
    const values = new Map<string, string>();
    vi.stubGlobal("sessionStorage", {
      setItem: (key: string, value: string) => values.set(key, value),
      getItem: (key: string) => values.get(key),
      removeItem: (key: string) => values.delete(key),
    });
    const deployment = await import("../../src/lib/deployment");
    expect(deployment.apiBaseUrl()).toBe("https://example.test" + prefix);
    expect(deployment.deploymentUrl("/api/v1/runs")).toBe(
      prefix + "/api/v1/runs",
    );
    expect(deployment.fragmentHref("#main-content")).toBe(
      prefix + "/runs/123?tab=log#main-content",
    );
    const session = await import("../../src/lib/session");
    session.saveSession("test-token", 300);
    expect(
      values.has("tradegrad.session.v1" + (prefix ? ":" + prefix : "")),
    ).toBe(true);
    expect(session.readSession()?.accessToken).toBe("test-token");
    session.clearSession();
    expect(session.readSession()).toBeNull();
  },
);

it("router strips prefix from logical return paths and adds it exactly once", async () => {
  const { createRouter, createRootRoute, createRoute, createMemoryHistory } =
    await import("@tanstack/react-router");
  const root = createRootRoute();
  const routeTree = root.addChildren([
    createRoute({ getParentRoute: () => root, path: "/runs" }),
    createRoute({ getParentRoute: () => root, path: "/login" }),
  ]);
  const router = createRouter({
    routeTree,
    basepath: "/lab/research-7",
    history: createMemoryHistory({
      initialEntries: ["/lab/research-7/runs?tab=log"],
    }),
  });
  await router.load();
  expect(router.state.location.pathname).toBe("/runs");
  expect(router.state.location.href).toBe("/runs?tab=log");
  expect(router.state.location.publicHref).toBe("/lab/research-7/runs?tab=log");
});
