import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";

const action = vi.hoisted(() => ({ pending: false }));
vi.mock("../../src/lib/use-data-action", () => ({
  useDataAction: () => ({
    pending: action.pending,
    error: null,
    run: vi.fn(),
    resetError: vi.fn(),
  }),
}));
afterEach(() => {
  vi.unstubAllGlobals();
  action.pending = false;
});

it("keeps the account selector label independent from option text", async () => {
  vi.stubGlobal("window", { location: { origin: "http://settings.test" } });
  const { SettingsManagement } =
    await import("../../src/features/settings-management");
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  query.setQueryData(["me"], { id: "admin", is_superuser: true });
  query.setQueryData(["managed-users", 0], {
    count: 1,
    data: [
      { id: "ordinary", email: "ordinary@example.com", is_superuser: false },
    ],
  });
  const html = renderToString(
    createElement(
      QueryClientProvider,
      { client: query },
      createElement(SettingsManagement),
    ),
  );
  expect(html).toContain(
    '<label for="managed-account-selector">Manage account</label>',
  );
  expect(html).toContain('<select id="managed-account-selector">');
  expect(html).toContain('<option value="ordinary">ordinary@example.com');
  query.clear();
});

it.each([false, true])(
  "locks profile and default inputs while save is pending: %s",
  async (pending) => {
    action.pending = pending;
    vi.stubGlobal("window", { location: { origin: "http://settings.test" } });
    const { SettingsManagement } =
      await import("../../src/features/settings-management");
    const { capabilityFixture } = await import("./research-fixtures");
    const query = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    query.setQueryData(["me"], {
      id: "owner",
      full_name: "Owner",
      is_superuser: false,
    });
    query.setQueryData(["workspace-defaults"], {
      initial_cash: 250000,
      max_iterations: 3,
      max_model_calls: 20,
      max_tokens: 50000,
      max_runtime_seconds: 600,
      model_profile_id: "fixture",
    });
    const html = renderToString(
      createElement(
        QueryClientProvider,
        { client: query },
        createElement(SettingsManagement, {
          capabilities: capabilityFixture(),
        }),
      ),
    );
    const fields = html.match(/<(?:input|select)\b[^>]*>/g) || [];
    expect(fields).toHaveLength(7);
    for (const field of fields)
      expect(field.includes('disabled=""')).toBe(pending);
    query.clear();
  },
);
