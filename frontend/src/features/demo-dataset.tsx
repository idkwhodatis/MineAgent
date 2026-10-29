import { useEffect, useRef, useState } from "react";
import { ErrorState, Panel } from "@/components/common";
import type { StructuredDemoReceipt } from "@/client/types.gen";
import { createDemoDataset } from "@/lib/demo-dataset";

export function DemoDatasetOnboarding({
  disabled,
  onApply,
}: {
  disabled: boolean;
  onApply: (receipt: StructuredDemoReceipt) => void;
}) {
  const [acknowledged, setAcknowledged] = useState(false);
  const [pending, setPending] = useState(false);
  const [receipt, setReceipt] = useState<StructuredDemoReceipt | null>(null);
  const [error, setError] = useState<unknown>(null);
  const active = useRef(true),
    lock = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  async function create() {
    if (disabled || lock.current || !acknowledged) return;
    lock.current = true;
    setPending(true);
    setError(null);
    try {
      const result = await createDemoDataset(acknowledged);
      if (active.current) setReceipt(result);
    } catch (failure) {
      if (active.current) setError(failure);
    } finally {
      lock.current = false;
      if (active.current) setPending(false);
    }
  }
  return (
    <Panel title="Start with a tiny synthetic dataset">
      <p>
        This demo is synthetic by construction, not live-market or point-in-time
        evidence. Creating it does not start research, enable a model, or
        release any result.
      </p>
      <label>
        <input
          type="checkbox"
          checked={acknowledged}
          disabled={disabled || pending}
          onChange={(event) => setAcknowledged(event.target.checked)}
        />
        I understand this is synthetic demonstration data.
      </label>
      <button
        type="button"
        disabled={disabled || pending || !acknowledged}
        onClick={() => void create()}
      >
        {pending
          ? "Retaining synthetic dataset…"
          : "Create synthetic demo dataset"}
      </button>
      {error != null && <ErrorState error={error} />}
      {receipt && (
        <>
          <p>
            Retained: {receipt.dataset.name}. No research was submitted.
            Repeating creation returns the same owned dataset.
          </p>
          <button
            type="button"
            disabled={disabled || pending || !acknowledged}
            onClick={() => onApply(receipt)}
          >
            Use returned demo preset
          </button>
          <p>
            Applying replaces the dataset/task/objective fields only. Review
            them and separately confirm source declarations, then run normal
            preflight. Model, budget and steer selections are not changed.
          </p>
        </>
      )}
    </Panel>
  );
}
