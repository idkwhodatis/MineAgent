import { useState } from "react";
import { ErrorState } from "@/components/common";
import {
  cancelEngineRun,
  engineRunActive,
  type EngineControlReceipt,
} from "@/lib/engine-submission-api";
import type { EngineDetail } from "@/lib/engine-runs-api";

export function EngineControls({
  run,
  reload,
}: {
  run: EngineDetail;
  reload: () => Promise<unknown>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [receipt, setReceipt] = useState<EngineControlReceipt | null>(null);
  async function cancel() {
    if (busy || !run.actions.cancel) return;
    setBusy(true);
    setError(null);
    try {
      setReceipt(await cancelEngineRun(run.id));
      await reload();
    } catch (failure) {
      setError(failure);
      await reload();
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      {run.actions.cancel && (
        <button type="button" disabled={busy} onClick={() => void cancel()}>
          {busy ? "Requesting cancellation…" : "Cancel RD-Agent run"}
        </button>
      )}
      {run.status === "cancel_requested" ||
      (receipt?.pending && engineRunActive(run.status)) ? (
        <p role="status">
          Cancellation requested. Physical sandbox stop has not yet been
          confirmed; waiting for authoritative status.
        </p>
      ) : (
        receipt && (
          <p role="status">
            Server reports {run.status}. A cancel response is not a new
            execution or resume grant.
          </p>
        )
      )}
      <p>
        Pause and resume unavailable for this fresh-run profile.{" "}
        <code>{run.actions.reason_code}</code>
      </p>
      {error != null && (
        <ErrorState error={error} retry={() => void reload()} />
      )}
    </div>
  );
}
