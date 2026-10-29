import type { DatasetPublic } from "@/client/types.gen";
import { factorRequest } from "./factor-api";
import { readSession } from "./session";

export type BasketRequest = {
  source_dataset_ids: [string, string];
  acknowledge_unverified: true;
  idempotency_key: string;
};
export type BasketReceipt = {
  dataset: DatasetPublic;
  profile: "mineagent.declared-upload-basket.v1";
  data_evidence: "user_declaration_unverified";
  is_synthetic: false;
  point_in_time_proven: false;
  research_submitted: false;
  result_released: false;
};
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function basketRequest(ids: string[], key: string): BasketRequest {
  if (
    ids.length !== 2 ||
    new Set(ids).size !== 2 ||
    ids.some((id) => !uuid.test(id)) ||
    !/^[A-Za-z0-9_.:-]{1,128}$/.test(key)
  )
    throw new Error("Select two distinct retained uploads.");
  return {
    source_dataset_ids: [...ids].sort() as [string, string],
    acknowledge_unverified: true,
    idempotency_key: key,
  };
}
export function verifyBasketReceipt(value: BasketReceipt): BasketReceipt {
  if (
    value.profile !== "mineagent.declared-upload-basket.v1" ||
    value.data_evidence !== "user_declaration_unverified" ||
    value.is_synthetic !== false ||
    value.point_in_time_proven !== false ||
    value.research_submitted !== false ||
    value.result_released !== false ||
    value.dataset.is_synthetic !== false ||
    value.dataset.source !== "declared_upload_basket" ||
    !uuid.test(value.dataset.id)
  )
    throw new Error("Declared upload receipt could not be verified.");
  return value;
}
export async function createBasket(
  body: BasketRequest,
): Promise<BasketReceipt> {
  if (
    body.acknowledge_unverified !== true ||
    JSON.stringify(body) !==
      JSON.stringify(
        basketRequest(body.source_dataset_ids, body.idempotency_key),
      )
  )
    throw new Error("Exact explicit upload composition required.");
  const token = readSession()?.accessToken;
  if (!token) throw new Error("Sign in before composing owned uploads.");
  const result = await factorRequest<BasketReceipt>(
    "/data/declared-upload-baskets",
    { method: "POST", body },
  );
  if (readSession()?.accessToken !== token)
    throw new Error("Session changed; inspect the owned dataset list.");
  return verifyBasketReceipt(result);
}
