import type { StructuredDemoReceipt } from "@/client/types.gen";
import { factorRequest } from "./factor-api";
import { readSession } from "./session";

export function verifyDemoReceipt(
  receipt: StructuredDemoReceipt,
): StructuredDemoReceipt {
  const qualification = "synthetic_by_construction_not_live_or_pit_evidence";
  if (
    receipt.qualification !== qualification ||
    receipt.research_submitted !== false ||
    receipt.dataset.is_synthetic !== true ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(
      receipt.dataset.id,
    ) ||
    receipt.rd_preset.qualification !== qualification ||
    receipt.rd_preset.acknowledgement_required !== true
  ) {
    throw new Error("Synthetic dataset receipt could not be verified.");
  }
  return receipt;
}

export async function createDemoDataset(
  acknowledged: boolean,
): Promise<StructuredDemoReceipt> {
  if (!acknowledged)
    throw new Error("Explicit synthetic-data confirmation required.");
  const token = readSession()?.accessToken;
  if (!token) throw new Error("Sign in before creating a demo dataset.");
  const receipt = await factorRequest<StructuredDemoReceipt>(
    "/engine-runs/demo-dataset",
    {
      method: "POST",
      body: { acknowledge_synthetic: true },
    },
  );
  if (readSession()?.accessToken !== token)
    throw new Error("Session changed; reload the owned dataset list.");
  return verifyDemoReceipt(receipt);
}
