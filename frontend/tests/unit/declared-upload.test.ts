import { createElement } from "react";
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { basketRequest, verifyBasketReceipt } from "@/lib/declared-upload";
import { DeclaredUploadBasket } from "@/features/declared-upload-basket";
const a = "00000000-0000-0000-0000-000000000002",
  b = "00000000-0000-0000-0000-000000000001";
describe("declared upload composition", () => {
  it("uses canonical distinct identities and an unchanged retry key", () => {
    expect(basketRequest([a, b], "same-key")).toEqual({
      source_dataset_ids: [b, a],
      acknowledge_unverified: true,
      idempotency_key: "same-key",
    });
  });
  it.each([[[a, a]], [[a]], [[a, "not-an-id"]]])(
    "rejects invalid input identities",
    (ids) => {
      expect(() => basketRequest(ids, "key")).toThrow();
    },
  );
  it("does not turn a synthetic or unverified reply into successful real-data publication", () => {
    const value: any = {
      dataset: { id: a, source: "declared_upload_basket", is_synthetic: false },
      profile: "mineagent.declared-upload-basket.v1",
      data_evidence: "user_declaration_unverified",
      is_synthetic: false,
      point_in_time_proven: false,
      research_submitted: false,
      result_released: false,
    };
    expect(verifyBasketReceipt(value)).toBe(value);
    for (const patch of [
      { point_in_time_proven: true },
      { is_synthetic: true },
      { result_released: true },
      { research_submitted: true },
      { data_evidence: "verified" },
    ])
      expect(() => verifyBasketReceipt({ ...value, ...patch })).toThrow();
  });
  it("shows an opt-in unverified declaration without automatic research or release", () => {
    const html = renderToStaticMarkup(
      createElement(DeclaredUploadBasket, {
        ownerId: a,
        datasets: [],
        disabled: false,
        onCreated: () => {
          throw new Error("must not execute during view");
        },
      }),
    );
    expect(html).toContain("unverified research assumptions");
    expect(html).toContain("No prices or actions are filled in");
    expect(html).not.toContain('checked=""');
    expect(html).toContain('disabled=""');
  });
});
