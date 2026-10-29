import { deploymentUrl } from "./deployment";
import { readSession } from "./session";
export type InputContentReference = {
  kind: "raw" | "normalized" | "feedback";
  id: string;
  sha256: string;
};
export type InputContent = {
  blob: Blob;
  text: string | null;
  filename: string;
};
export async function readInputContent(
  ref: InputContentReference,
  signal?: AbortSignal,
): Promise<InputContent> {
  if (
    !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(
      ref.id,
    ) ||
    !/^[a-f0-9]{64}$/.test(ref.sha256)
  )
    throw new Error("Exact input reference required.");
  const session = readSession();
  if (!session) throw new Error("Sign in to view retained input.");
  const normalized = ref.kind !== "raw";
  const route = {
    raw: "external-objects",
    normalized: "versions",
    feedback: "feedback-versions",
  }[ref.kind];
  if (!route) throw new Error("Unsupported input content kind.");
  const response = await fetch(
    deploymentUrl(
      `/api/v1/research-inputs/${route}/${ref.id}/content?expected_sha256=${ref.sha256}`,
    ),
    {
      headers: { Authorization: `Bearer ${session.accessToken}` },
      cache: "no-store",
      signal: AbortSignal.any([
        ...(signal ? [signal] : []),
        AbortSignal.timeout(20000),
      ]),
    },
  );
  if (!response.ok)
    throw new Error(`Retained content is unavailable (${response.status}).`);
  if (!response.body) throw new Error("Missing retained content response.");
  const limit = normalized ? 32768 : 65536;
  const reader = response.body.getReader();
  const bytes = new Uint8Array(limit);
  let size = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      if (size + value.byteLength > limit)
        throw new Error("Retained content exceeds its bound.");
      bytes.set(value, size);
      size += value.byteLength;
    }
  } finally {
    await reader.cancel();
    reader.releaseLock();
  }
  const content = bytes.slice(0, size);
  const hash = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", content)),
    (value) => value.toString(16).padStart(2, "0"),
  ).join("");
  if (
    hash !== ref.sha256 ||
    response.headers.get("X-Content-SHA256") !== ref.sha256
  )
    throw new Error("Retained content commitment differs.");
  if (readSession()?.accessToken !== session.accessToken)
    throw new Error("Session changed; content was discarded.");
  let text: string | null = null;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(content);
  } catch {
    /* Opaque bytes remain downloadable, never lossy rendered. */
  }
  return {
    blob: new Blob([content], {
      type: normalized ? "application/json" : "application/octet-stream",
    }),
    text,
    filename:
      ref.kind === "feedback"
        ? `feedback-input-${ref.id}.json`
        : normalized
          ? `normalized-input-${ref.id}.json`
          : `external-input-${ref.id}.bin`,
  };
}
