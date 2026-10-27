import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";

const view = vi.hoisted(() => ({ configured: false }));
vi.mock("@tanstack/react-router", () => ({
  useSearch: () =>
    view.configured ? { parent_factor_version_id: "parent" } : {},
  useNavigate: () => () => undefined,
  Link: ({ children }: { children: unknown }) => children,
}));
vi.mock("@tanstack/react-query", async (original) => ({
  ...(await original<typeof import("@tanstack/react-query")>()),
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => ({
    isPending: false,
    data:
      queryKey[0] !== "engine-history"
        ? { id: "owner", data: [] }
        : queryKey[1] === "capabilities"
          ? {
              engines: [
                {
                  engine: "rdagent",
                  purpose: "factor_research",
                  can_preflight: true,
                  can_create: false,
                  model_profiles: [],
                },
              ],
            }
          : {
              data: [
                {
                  id: "dataset",
                  name: "Owned sample",
                  market: "US",
                  start_date: "2024-01-01",
                  end_date: "2024-02-01",
                  eligibility: "not_checked",
                  is_synthetic: true,
                },
              ],
              count: 1,
            },
  }),
}));
import { EnginePreflightPage } from "../../src/features/engine-preflight";

it("keeps exact select labels independent of option text on each mode and render", () => {
  // Markup regression, not a replacement for real root/subpath/research acceptance.
  for (const configured of [false, true]) {
    view.configured = configured;
    for (let render = 0; render < 2; render++) {
      const html = renderToStaticMarkup(createElement(EnginePreflightPage));
      const labels = [
        ["engine-retained-dataset", "Retained dataset"],
        ["engine-generation-mode", "Generation mode"],
        ["engine-factor-kind", "Factor kind"],
        ["engine-interpretation", "Interpretation"],
        ["engine-objective-metric", "Objective metric"],
        ["engine-threshold-direction", "Threshold direction"],
        ...(configured
          ? [["engine-model-profile", "Configured model profile"]]
          : []),
      ];
      for (const [id, name] of labels) {
        expect(html).toContain(`<label for="${id}">${name}</label>`);
        expect(html).toContain(`<select id="${id}"`);
      }
      expect(html).toContain("Owned sample");
      expect(html).not.toMatch(/<label[^>]*>(?:(?!<\/label>).)*<select/);
    }
  }
});
