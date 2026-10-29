import { useEffect, useRef, useState } from "react";
import type { EngineDatasetOption } from "@/client/types.gen";
import { Panel, ErrorState } from "@/components/common";
import {
  basketRequest,
  createBasket,
  type BasketRequest,
  type BasketReceipt,
} from "@/lib/declared-upload";

export function DeclaredUploadBasket({
  ownerId,
  datasets,
  disabled,
  onCreated,
}: {
  ownerId: string;
  datasets: EngineDatasetOption[];
  disabled: boolean;
  onCreated: () => void;
}) {
  const key = `mineagent.declared-upload-draft.v1:${ownerId}`;
  const [ids, setIds] = useState<[string, string]>(["", ""]);
  const [ack, setAck] = useState(false);
  const [saved, setSaved] = useState<BasketRequest | null>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (!raw || raw.length > 4096) return null;
      const x = JSON.parse(raw);
      if (x.acknowledge_unverified !== true) return null;
      return basketRequest(x.source_dataset_ids, x.idempotency_key);
    } catch {
      return null;
    }
  });
  const [pending, setPending] = useState(false),
    [error, setError] = useState<unknown>(null),
    [receipt, setReceipt] = useState<BasketReceipt | null>(null);
  const lock = useRef(false),
    active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  async function submit() {
    if (disabled || lock.current || !ack) return;
    lock.current = true;
    setPending(true);
    setError(null);
    try {
      const body = saved ?? basketRequest(ids, crypto.randomUUID());
      localStorage.setItem(key, JSON.stringify(body));
      setSaved(body);
      const value = await createBasket(body);
      if (active.current) {
        setReceipt(value);
        onCreated();
      }
    } catch (e) {
      if (active.current) setError(e);
    } finally {
      lock.current = false;
      if (active.current) setPending(false);
    }
  }
  const choices = datasets.filter(
    (d) => d.source === "upload" && !d.is_synthetic,
  );
  return (
    <Panel title="Use two declared real-data uploads">
      <p>
        Choose two existing owned CSV uploads on this dataset page: XNYS, USD,
        daily raw OHLCV in shares, identical complete dates and explicit
        no-corporate-actions declarations. No prices or actions are filled in.
        Availability and fixed membership remain unverified research
        assumptions, not historical point-in-time proof.
      </p>
      {[0, 1].map((index) => (
        <div key={index}>
          <label htmlFor={`declared-upload-source-${index}`}>
            Upload {index + 1}
          </label>
          <select
            id={`declared-upload-source-${index}`}
            aria-label={`Declared source ${index + 1}`}
            value={saved?.source_dataset_ids[index] ?? ids[index]}
            disabled={disabled || pending || Boolean(saved)}
            onChange={(e) =>
              setIds((old) => {
                const n = [...old] as [string, string];
                n[index] = e.target.value;
                return n;
              })
            }
          >
            <option value="">Select an existing upload</option>
            {choices.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} ({d.start_date}–{d.end_date})
              </option>
            ))}
          </select>
        </div>
      ))}
      <label>
        <input
          type="checkbox"
          checked={ack}
          disabled={disabled || pending}
          onChange={(e) => setAck(e.target.checked)}
        />
        I understand these are user declarations, not verified data or synthetic
        examples.
      </label>
      <button
        type="button"
        disabled={
          disabled || pending || !ack || (!saved && (!ids[0] || !ids[1]))
        }
        onClick={() => void submit()}
      >
        {pending
          ? "Retaining declared uploads…"
          : saved
            ? "Check / retry the same composition"
            : "Create declared upload basket"}
      </button>
      {saved && (
        <p>
          The exact creation key and selection are retained across reload. An
          uncertain response is not success; retrying uses the same key, never a
          second composition.
        </p>
      )}
      {error != null && <ErrorState error={error} />}
      {receipt && (
        <>
          <p>
            Retained {receipt.dataset.name}: {receipt.dataset.id}. Select this
            dataset below and review the research objective (two listings
            require an appropriate minimum-pair protocol). No research or result
            release was performed.
          </p>
          <button
            type="button"
            disabled={pending || disabled}
            onClick={() => {
              localStorage.removeItem(key);
              setSaved(null);
              setReceipt(null);
              setAck(false);
            }}
          >
            Prepare another selection
          </button>
        </>
      )}
    </Panel>
  );
}
