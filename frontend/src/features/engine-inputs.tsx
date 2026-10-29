import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Loading, Panel } from "@/components/common";
import { deploymentUrl } from "@/lib/deployment";
import {
  engineInputsOptions,
  groupInputs,
  groupWorkflowInputs,
} from "@/lib/engine-inputs";
import type { ResearchInputs } from "@/client/types.gen";
const titles = {
  user_provided: "User-provided submissions (pinned)",
  workflow_stage: "Workflow-selected frozen inputs",
  agent_generated: "Agent-generated proposals",
  unknown: "Origin not established",
};
export function InputOriginGroups({ data }: { data: ResearchInputs }) {
  return (
    <section aria-label="Research inputs by origin">
      <p>
        Input origin, not machine-learning supervision. A recorded submission
        does not establish who authored every field. A displayed proposal is not
        human approval, adoption or execution permission.
      </p>
      {groupInputs(data.items).map((group) => (
        <Panel key={group.origin} title={titles[group.origin]}>
          {group.origin === "user_provided" && (
            <p>
              User-provided inputs stay pinned first across refreshes. Pinning
              is display-only and never changes the immutable request or
              approves a proposal.
            </p>
          )}
          {!group.items.length && (
            <p>No recorded inputs with this origin on this page.</p>
          )}
          {group.items.map((item) => (
            <article
              key={`${item.origin}:${item.id}:${item.content_sha256 ?? "unknown"}`}
              className="mb-4"
            >
              <h3>{item.title}</h3>
              {group.origin === "user_provided" && item.default_pinned && (
                <strong aria-label="Pinned user input">Pinned</strong>
              )}
              <p>
                {item.created_at} · {item.id}
              </p>
              <p>Provenance: {item.provenance}</p>
              {item.content_sha256 && (
                <p className="break-all">
                  Input commitment: {item.content_sha256}
                </p>
              )}
              {item.submission && item.origin === "user_provided" && (
                <details open>
                  <summary>Exact recorded submission declarations</summary>
                  <pre>{JSON.stringify(item.submission, null, 2)}</pre>
                </details>
              )}
              {item.origin === "user_provided" && item.steer_snapshot && (
                <section aria-label="Frozen submission steer inputs">
                  <h4>Steer versions frozen at submission</h4>
                  <p>
                    Later edits or disabling a steer do not replace these
                    inputs. This is an accepted input snapshot, not proof that a
                    model consumed or complied with the guidance. Free-text
                    constraints are guidance, not verified platform rules.
                  </p>
                  <p className="break-all">
                    Snapshot SHA-256: {item.steer_snapshot_sha256}
                  </p>
                  <p className="break-all">
                    Model binding SHA-256: {item.model_binding_sha256}
                  </p>
                  {!(item.steer_snapshot.versions ?? []).length && (
                    <p>No steer versions selected.</p>
                  )}
                  <ol>
                    {(item.steer_snapshot.versions ?? []).map((version) => (
                      <li key={version.version_id}>
                        <p>
                          {version.kind} · version {version.ordinal} ·{" "}
                          {version.version_id}
                        </p>
                        <p className="whitespace-pre-wrap">{version.text}</p>
                        <p className="break-all">
                          Text SHA-256: {version.text_sha256}
                        </p>
                      </li>
                    ))}
                  </ol>
                </section>
              )}
              {item.origin === "workflow_stage" && item.workflow_inputs && (
                <section aria-label="Frozen workflow inputs by origin">
                  <p>
                    Workflow-selected versions, not a new user submission.
                    Selection does not prove model consumption, compliance or
                    approval.
                  </p>
                  <p className="break-all">
                    Combined input SHA-256: {item.workflow_inputs_sha256}
                  </p>
                  <p>
                    Stage: {item.workflow_inputs.binding.stage_id}. Input
                    revision: {item.workflow_inputs.selection.revision_id}.
                  </p>
                  {groupWorkflowInputs(item.workflow_inputs.inputs ?? []).map(
                    (group) => (
                      <div key={group.origin}>
                        <h4>
                          {group.origin === "manual_user_submission"
                            ? "User manual guidance (pinned)"
                            : group.origin === "normalized_external_proposal"
                              ? "Normalized external evidence"
                              : group.origin === "experiment_feedback_proposal"
                                ? "Derived experiment feedback"
                                : "Origin not established"}
                        </h4>
                        {group.items.map(({ input, position }) => (
                          <article key={input.ref.version_id}>
                            {group.origin === "manual_user_submission" && (
                              <strong aria-label="Pinned manual guidance">
                                Pinned manual guidance
                              </strong>
                            )}
                            <p>
                              Frozen position {position} ·{" "}
                              {input.ref.version_id}
                            </p>
                            <p>{input.title}</p>
                            <p className="whitespace-pre-wrap">{input.text}</p>
                            <p className="break-all">
                              Text SHA-256: {input.text_sha256}
                            </p>
                          </article>
                        ))}
                      </div>
                    ),
                  )}
                </section>
              )}
              {item.factor_id && item.factor_version_id && (
                <a
                  href={deploymentUrl(
                    `/factors/${item.factor_id}/versions/${item.factor_version_id}`,
                  )}
                >
                  Open exact proposed factor version {item.factor_version_id}
                </a>
              )}
              {item.origin === "agent_generated" && (
                <>
                  <p>
                    Model-response provenance only; raw model messages, provider
                    headers and unreleased result values are not displayed here.
                  </p>
                  <ul>
                    {item.operations?.map((operation) => (
                      <li key={operation.operation_id}>
                        Model operation {operation.operation_id}; request
                        SHA-256 {operation.request_sha256}; response SHA-256{" "}
                        {operation.response_sha256}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </article>
          ))}
        </Panel>
      ))}
    </section>
  );
}
export function EngineInputs({ runId }: { runId: string }) {
  const [offset, setOffset] = useState(0);
  const inputs = useQuery(engineInputsOptions(runId, offset));
  if (inputs.isPending) return <Loading />;
  if (inputs.error)
    return (
      <ErrorState error={inputs.error} retry={() => void inputs.refetch()} />
    );
  if (inputs.data.run_id !== runId)
    return (
      <ErrorState
        error={
          new Error("Input provenance differs from the exact selected run.")
        }
      />
    );
  return (
    <>
      <InputOriginGroups data={inputs.data} />
      <button
        disabled={offset === 0}
        onClick={() => setOffset(Math.max(0, offset - 25))}
      >
        Previous input proposals
      </button>
      <button
        disabled={!inputs.data.has_more_proposals}
        onClick={() => setOffset(offset + 25)}
      >
        Next input proposals
      </button>
    </>
  );
}
