import { useEffect, useRef, useState } from "react";
import {
  readInputContent,
  type InputContent,
  type InputContentReference,
} from "@/lib/research-input-content";
import { watchSession } from "@/lib/session";

export function InputContentActions({
  reference,
}: {
  reference: InputContentReference;
}) {
  // Exact immutable identity remounts the private buffer when navigation changes.
  return (
    <ContentActions
      key={`${reference.kind}:${reference.id}:${reference.sha256}`}
      reference={reference}
    />
  );
}
function ContentActions({ reference }: { reference: InputContentReference }) {
  const [content, setContent] = useState<InputContent | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef(true),
    controller = useRef<AbortController | null>(null);
  useEffect(() => {
    active.current = true;
    const unwatch = watchSession(() => {
      controller.current?.abort();
      setContent(null);
      setError(null);
    });
    return () => {
      active.current = false;
      controller.current?.abort();
      unwatch();
    };
  }, []);
  async function load(download: boolean) {
    if (controller.current) return;
    const abort = new AbortController();
    controller.current = abort;
    setPending(true);
    setError(null);
    setContent(null);
    try {
      const value = await readInputContent(reference, abort.signal);
      if (!active.current || abort.signal.aborted) return;
      if (download) {
        const url = URL.createObjectURL(value.blob);
        try {
          const anchor = document.createElement("a");
          anchor.href = url;
          anchor.download = value.filename;
          anchor.click();
        } finally {
          URL.revokeObjectURL(url);
        }
      } else setContent(value);
    } catch {
      if (active.current && !abort.signal.aborted)
        setError(
          "Content could not be verified or is unavailable. No text was disclosed.",
        );
    } finally {
      controller.current = null;
      if (active.current) setPending(false);
    }
  }
  return (
    <div>
      <button type="button" disabled={pending} onClick={() => void load(false)}>
        View{" "}
        {reference.kind === "raw"
          ? "input"
          : reference.kind === "feedback"
            ? "feedback output"
            : "normalized output"}
      </button>
      <button type="button" disabled={pending} onClick={() => void load(true)}>
        Download retained {reference.kind === "raw" ? "input" : "output"}
      </button>
      {pending && <p role="status">Verifying retained bytes…</p>}
      {error && <p role="alert">{error}</p>}
      {content && (
        <section aria-label="Explicitly requested retained content">
          <p>
            Passive untrusted data. Viewing is not approval, model consumption
            or a workflow selection.
          </p>
          {content.text === null ? (
            <p>
              These bytes are not valid UTF-8; download the opaque input
              instead.
            </p>
          ) : (
            <pre>{content.text}</pre>
          )}
          <button type="button" onClick={() => setContent(null)}>
            Close content
          </button>
        </section>
      )}
    </div>
  );
}
