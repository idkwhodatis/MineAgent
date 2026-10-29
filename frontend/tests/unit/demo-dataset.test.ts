import { afterEach, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { StructuredDemoReceipt } from "../../src/client/types.gen";

const qualification =
  "synthetic_by_construction_not_live_or_pit_evidence" as const;
const receipt = {
  dataset: {
    id: "10000000-0000-4000-8000-000000000001",
    name: "Tiny demo",
    is_synthetic: true,
  },
  qualification,
  research_submitted: false,
  rd_preset: { qualification, acknowledgement_required: true },
} as StructuredDemoReceipt;
afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});
function session() {
  vi.stubGlobal("sessionStorage", {
    getItem: () =>
      JSON.stringify({
        accessToken: "owner-token",
        expiresAt: Date.now() + 60_000,
      }),
  });
}
it("requires explicit confirmation and authentication before any mutation", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const { createDemoDataset } = await import("../../src/lib/demo-dataset");
  await expect(createDemoDataset(false)).rejects.toThrow("confirmation");
  await expect(createDemoDataset(true)).rejects.toThrow("Sign in");
  expect(fetcher).not.toHaveBeenCalled();
});
it.each(["", "/mineagent"])(
  "retains only synthetic data through authenticated %s API, never starts research",
  async (prefix) => {
    vi.stubGlobal("document", { querySelector: () => ({ content: prefix }) });
    session();
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(receipt)));
    vi.stubGlobal("fetch", fetcher);
    const { createDemoDataset } = await import("../../src/lib/demo-dataset");
    expect(await createDemoDataset(true)).toEqual(receipt);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe(
      `${prefix}/api/v1/engine-runs/demo-dataset`,
    );
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      method: "POST",
      headers: { Authorization: "Bearer owner-token" },
      body: '{"acknowledge_synthetic":true}',
    });
  },
);
it("does not attach a response after the authentication session changed", async () => {
  session();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async () => {
      vi.stubGlobal("sessionStorage", { getItem: () => null });
      return new Response(JSON.stringify(receipt));
    }),
  );
  const { createDemoDataset } = await import("../../src/lib/demo-dataset");
  await expect(createDemoDataset(true)).rejects.toThrow("Session changed");
});
it("propagates API errors without inventing a dataset or eligibility", async () => {
  session();
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response('{"code":"neutral_busy"}', { status: 409 }),
      ),
  );
  const { createDemoDataset } = await import("../../src/lib/demo-dataset");
  await expect(createDemoDataset(true)).rejects.toMatchObject({
    status: 409,
    code: "neutral_busy",
  });
});
it.each([
  { ...receipt, research_submitted: true },
  { ...receipt, dataset: { ...receipt.dataset, is_synthetic: false } },
  {
    ...receipt,
    rd_preset: { ...receipt.rd_preset, acknowledgement_required: false },
  },
])("rejects contradictory receipt qualification", async (invalid) => {
  const { verifyDemoReceipt } = await import("../../src/lib/demo-dataset");
  expect(() => verifyDemoReceipt(invalid as StructuredDemoReceipt)).toThrow();
});
it("renders an unchecked explicit confirmation and no automatic preset application", async () => {
  const { DemoDatasetOnboarding } =
    await import("../../src/features/demo-dataset");
  const apply = vi.fn();
  const html = renderToStaticMarkup(
    createElement(DemoDatasetOnboarding, { disabled: false, onApply: apply }),
  );
  expect(html).toContain("not live-market or point-in-time evidence");
  expect(html).not.toContain('checked=""');
  expect(html).toContain('disabled=""');
  expect(html).not.toContain("Use returned demo preset");
  expect(apply).not.toHaveBeenCalled();
});
