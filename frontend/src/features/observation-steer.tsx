import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ErrorState, Loading } from "@/components/common";
import { readSession } from "@/lib/session";
import { deploymentUrl } from "@/lib/deployment";
import { steerCapabilityOptions, type SteerView } from "@/lib/rd-steers";
import {
  observationContext,
  observationSteerText,
  saveReviewedObservation,
  watchObservationSession,
  type ResearchObservation,
} from "@/lib/observation-steer";

function ObservationEditor({
  context,
  close,
  sessionToken,
}: {
  sessionToken?: string;
  context: string;
  close: () => void;
}) {
  const capability = useQuery(steerCapabilityOptions);
  const client = useQueryClient();
  const sent = useRef(false);
  const [guidance, setGuidance] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [pending, setPending] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [saved, setSaved] = useState<SteerView>();
  const [error, setError] = useState<unknown>();
  if (capability.isPending) return <Loading />;
  if (capability.error) return <ErrorState error={capability.error} />;
  if (!capability.data.can_manage)
    return <p>Saving guidance unavailable: {capability.data.reason_code}.</p>;
  async function save() {
    if (!reviewed || pending || attempted || sent.current) return;

    try {
      if (!sessionToken || readSession()?.accessToken !== sessionToken)
        throw new Error(
          "Account changed; close this draft and reload under the current account.",
        );
      observationSteerText(guidance, context);
    } catch (failure) {
      setError(failure);
      return;
    }
    sent.current = true;
    setPending(true);
    setAttempted(true);
    setError(undefined);
    try {
      const result = await saveReviewedObservation(
        guidance,
        context,
        sessionToken,
      );
      setSaved(result);
      await client.invalidateQueries({ queryKey: ["rd-steers"] });
      await client.invalidateQueries({ queryKey: ["workflow-manual-inputs"] });
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }
  return (
    <section aria-label="Review observation-bound guidance">
      <h4>User-provided guidance — pinned by origin, not approval</h4>
      <p>
        This draft does not change a workflow. Observation IDs are evidence
        pointers, not enforced constraints or proof that a model followed them.
      </p>
      <pre className="whitespace-pre-wrap break-all">{context}</pre>
      <label>
        Your research direction
        <textarea
          value={guidance}
          maxLength={2048}
          disabled={attempted}
          onChange={(e) => {
            setGuidance(e.target.value);
            setReviewed(false);
          }}
        />
      </label>
      <label>
        <input
          type="checkbox"
          checked={reviewed}
          disabled={attempted}
          onChange={(e) => setReviewed(e.target.checked)}
        />
        I reviewed these exact references and my guidance.
      </label>
      <button
        disabled={!reviewed || !guidance.trim() || pending || attempted}
        onClick={() => void save()}
      >
        Save observation-bound steer version
      </button>
      {error != null && (
        <>
          <ErrorState error={error} />
          <p>
            Inspect saved steer history before retrying an uncertain write; this
            editor does not resend it.
          </p>
        </>
      )}
      {saved && (
        <div role="status">
          <p>
            Saved version {saved.current.version_id} · SHA-256{" "}
            {saved.current.text_sha256}.
          </p>
          <p>
            Saved, not selected or consumed. In the workflow’s future-stage
            inputs, refresh available manual versions, select this exact
            version, then save selection using its current revision. A running
            stage keeps its original inputs. Use pause-after-stage to wait at a
            safe checkpoint; resume only when authorized. Failed/unverified
            children cannot be resumed or silently forked.
          </p>
        </div>
      )}
      <a href={deploymentUrl("/rd-steers")}>
        Inspect persistent steer versions
      </a>
      <button disabled={pending} onClick={close}>
        Close observation draft
      </button>
    </section>
  );
}
export function ObservationSteer({
  observation,
  prepared = false,
  onClose,
}: {
  prepared?: boolean;
  onClose?: () => void;
  observation: ResearchObservation;
}) {
  const [sessionToken] = useState(() => readSession()?.accessToken);
  const [revoked, setRevoked] = useState(false);
  useEffect(
    () => watchObservationSession(sessionToken, () => setRevoked(true)),
    [sessionToken],
  );
  const [initial] = useState(() => {
    try {
      return {
        context: prepared ? observationContext(observation) : undefined,
        error: undefined as unknown,
      };
    } catch (error) {
      return { context: undefined, error };
    }
  });
  const [context, setContext] = useState<string | undefined>(initial.context);
  const [error, setError] = useState<unknown>(initial.error);
  if (!sessionToken || revoked || readSession()?.accessToken !== sessionToken)
    return (
      <p role="status">
        Account changed; observation context and saved version are hidden.
        Reload the source under the current account.
      </p>
    );
  return (
    <section aria-label="Steer from observed evidence">
      <button
        onClick={() => {
          try {
            if (readSession()?.accessToken !== sessionToken)
              throw new Error(
                "Account changed; reload the observation before preparing guidance.",
              );
            setContext(observationContext(observation));
            setError(undefined);
          } catch (failure) {
            setError(failure);
          }
        }}
      >
        Prepare guidance from this observation
      </button>
      {error != null && <ErrorState error={error} />}
      {context && (
        <ObservationEditor
          key={context}
          context={context}
          sessionToken={sessionToken}
          close={() => {
            setContext(undefined);
            onClose?.();
          }}
        />
      )}
    </section>
  );
}
