import { z } from "zod";
import { deploymentUrl } from "./deployment";
import { clearSession, readSession } from "./session";
import type { RetainedExternalInput } from "@/input-audit-contracts/types.gen";
const receipt = z.strictObject({
  id: z.uuid(),
  sha256: z.string().regex(/^[a-f0-9]{64}$/),
  size_bytes: z.number().int().min(0).max(65536),
  qualification: z.literal("raw_retained_not_normalized"),
  workflow_created: z.literal(false),
});
export function externalInputBytes(text: string) {
  const raw = new TextEncoder().encode(text);
  if (raw.byteLength > 65536)
    throw new Error("External input exceeds 64 KiB; it will not be truncated.");
  return raw;
}
export async function retainExternalInput(
  text: string,
  signal?: AbortSignal,
): Promise<RetainedExternalInput> {
  const raw = externalInputBytes(text);
  const hash = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", raw)),
    (value) => value.toString(16).padStart(2, "0"),
  ).join("");
  const session = readSession();
  if (!session)
    throw new Error("Sign in before retaining owner-scoped external input.");
  const response = await fetch(
    deploymentUrl("/api/v1/research-inputs/external-objects"),
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${session.accessToken}`,
        "Content-Type": "application/octet-stream",
      },
      body: new Blob([raw], { type: "application/octet-stream" }),
      signal: AbortSignal.any([
        ...(signal ? [signal] : []),
        AbortSignal.timeout(20000),
      ]),
    },
  );
  if (response.status === 401) clearSession(true);
  if (!response.ok)
    throw new Error(
      `External input was not acknowledged (${response.status}). Retain the exact text for a safe same-content retry.`,
    );
  const saved = receipt.parse(await response.json());
  if (readSession()?.accessToken !== session.accessToken)
    throw new Error(
      "The signed-in session changed; do not attach this receipt to another owner’s workflow.",
    );
  if (saved.sha256 !== hash || saved.size_bytes !== raw.byteLength)
    throw new Error(
      "Retained input receipt differs from the exact supplied bytes; do not create a workflow from it.",
    );
  return saved;
}
