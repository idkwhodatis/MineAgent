import { useState } from "react";
import { ErrorState } from "@/components/common";
import {
  externalInputBytes,
  retainExternalInput,
} from "@/lib/research-input-intake";
import type { RetainedExternalInput } from "@/input-audit-contracts/types.gen";

/** First step of the normal workflow entry, not a separately enabled page. */
export function RawExternalIntake({
  canRetain = false,
  reason,
  onRetained,
}: {
  canRetain?: boolean;
  reason: string;
  onRetained: (receipt: RetainedExternalInput) => void;
}) {
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState<RetainedExternalInput | null>(null);
  const [error, setError] = useState<unknown>(null);
  const bytes = new TextEncoder().encode(text).byteLength;
  async function retain() {
    if (!canRetain || pending) return;
    try {
      externalInputBytes(text);
      setPending(true);
      setError(null);
      const result = await retainExternalInput(text);
      setSaved(result);
      onRetained(result);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }
  return (
    <section aria-label="External source intake">
      <h3>Retain external JSON input</h3>
      <p>
        Paste passive source data. No URL is fetched and no external text is
        treated as an instruction or approval. The normalization stage will
        record format errors; intake does not certify valid or trustworthy
        content.
      </p>
      {!canRetain && <p>Intake unavailable: {reason}</p>}
      <label>
        Raw external JSON data
        <textarea
          value={text}
          disabled={pending}
          onChange={(event) => {
            setText(event.target.value);
            setSaved(null);
          }}
        />
      </label>
      <p>
        {bytes} / 65536 UTF-8 bytes. Oversized input is rejected, never
        truncated.
      </p>
      {error !== null && <ErrorState error={error} />}
      <button
        type="button"
        disabled={!canRetain || pending || bytes > 65536}
        onClick={() => void retain()}
      >
        {pending ? "Retaining exact bytes…" : "Retain raw input"}
      </button>
      {saved && (
        <section aria-label="Retained raw input receipt">
          <p>
            Retained object {saved.id}; {saved.size_bytes} bytes.
          </p>
          <p className="break-all">SHA-256: {saved.sha256}</p>
          <p>
            No workflow was created and no normalization has completed.
            Configure the mapping and workflow next.
          </p>
        </section>
      )}
      <p>
        Raw text is not saved to browser storage by this component. If an
        acknowledgement is lost, retrying the same owned bytes reuses the
        content-addressed object rather than creating a research run.
      </p>
    </section>
  );
}
