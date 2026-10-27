import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { requireFactorReadsIdle } from "../../src/lib/factor-publication";
import { FactorEditor } from "../../src/features/factor-workbench/editor";

function pending(client: QueryClient, kind: string) {
  let finish!: () => void;
  const response = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const request = client.fetchQuery({
    queryKey: ["factors", kind, "owned"],
    queryFn: async () => {
      await response;
      return {};
    },
    retry: false,
  });
  return { finish, request };
}
it("blocks every exposure-recording query until response completion without retrying publication", async () => {
  const client = new QueryClient();
  const post = vi.fn();
  for (const kind of [
    "list",
    "detail",
    "values",
    "sample",
    "comparison-values",
  ]) {
    const read = pending(client, kind);
    expect(() => {
      requireFactorReadsIdle(client);
      post();
    }).toThrow("exposure recording");
    expect(post).not.toHaveBeenCalled();
    read.finish();
    await read.request;
    expect(() => requireFactorReadsIdle(client)).not.toThrow();
  }
  expect(post).not.toHaveBeenCalled(); // no automatic mutation after the read ends
  client.clear();
});
it("rechecks after asynchronous preparation and leaves draft and retry identity unchanged", async () => {
  const client = new QueryClient();
  const draft = {
    code: "retained exact source",
    key: "existing unresolved key",
  };
  const post = vi.fn();
  requireFactorReadsIdle(client);
  const read = pending(client, "detail");
  await Promise.resolve();
  expect(() => {
    requireFactorReadsIdle(client);
    post(draft);
  }).toThrow();
  expect(draft).toEqual({
    code: "retained exact source",
    key: "existing unresolved key",
  });
  expect(post).not.toHaveBeenCalled();
  read.finish();
  await read.request;
  const metadata = pending(client, "import");
  expect(() => requireFactorReadsIdle(client)).not.toThrow();
  metadata.finish();
  await metadata.request;
  client.clear();
});
it("renders the actual editor save disabled during an owned exposure read", async () => {
  const client = new QueryClient();
  const read = pending(client, "list");
  const html = () =>
    renderToStaticMarkup(
      createElement(
        QueryClientProvider,
        { client },
        createElement(FactorEditor, {
          owner: "owner",
          onSaved: vi.fn(),
          onClose: vi.fn(),
        }),
      ),
    );
  expect(html()).toMatch(
    /<button[^>]*disabled=""[^>]*>Save exact definition<\/button>/,
  );
  read.finish();
  await read.request;
  expect(html()).not.toMatch(
    /<button[^>]*disabled=""[^>]*>Save exact definition<\/button>/,
  );
  client.clear();
});
