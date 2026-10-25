import { useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Loading, Panel } from "@/components/common";
import { Field, JsonDetails, Metadata } from "@/components/data-common";
import { Button } from "@/components/ui/button";
import {
  factorImportOptions,
  factorRequest,
  type FactorImportAdmission,
} from "@/lib/factor-api";
import {
  factorArchiveHash,
  importStillRunning,
  submissionKey,
} from "./selection";

export function FactorImportPanel({
  enabled,
  owner,
  admissionId,
  onAdmission,
}: {
  enabled: boolean;
  owner?: string;
  admissionId?: string;
  onAdmission: (id?: string) => void;
}) {
  const [file, setFile] = useState<File>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const active = useRef(false);
  async function upload() {
    if (!file || !owner || active.current || !enabled) return;
    active.current = true;
    setBusy(true);
    setError(undefined);
    try {
      const archive = await factorArchiveHash(file);
      const result = await factorRequest<FactorImportAdmission>(
        "/factor-packages/import",
        {
          method: "POST",
          body: file,
          key: submissionKey(owner, "import", {
            archive_sha256: archive,
            size_bytes: file.size,
          }),
        },
      );
      if (result.archive_sha256 !== archive)
        throw new Error(
          "Admission archive identity differs from the selected bytes. No result has been selected.",
        );
      onAdmission(result.id);
    } catch (err) {
      setError(err);
    } finally {
      active.current = false;
      setBusy(false);
    }
  }
  return (
    <Panel title="Import an immutable factor package">
      <p className="notice">
        {enabled
          ? "Import retains original package evidence and creates explicit local bindings. Source history stays unknown; parser validation is not a claim of point-in-time correctness or source-code execution."
          : "Binary package import is unavailable until isolated parser and crash-containment acceptance is complete. No in-process parsing fallback is used."}
      </p>
      {enabled && (
        <>
          <div className="factor-toolbar">
            <Field
              id="factor-import-file"
              label="Raw factor ZIP package"
              help="At most 10,000,000 bytes. Only bounded raw archives are accepted, never multipart forms or arbitrary paths."
            >
              <input
                id="factor-import-file"
                type="file"
                accept=".zip,application/zip"
                disabled={busy}
                onChange={(event) => {
                  setFile(event.target.files?.[0]);
                  setError(undefined);
                }}
              />
            </Field>
            <Button
              disabled={busy || !owner || !file}
              onClick={() => void upload()}
            >
              {busy ? "Submitting archive…" : "Import exact package"}
            </Button>
          </div>
          <p className="muted">
            A lost upload response does not prove failure. Reselecting identical
            bytes and retrying uses the same submission key, including after
            reload.
          </p>
        </>
      )}
      {error !== undefined && <ErrorState error={error} />}
      {admissionId && (
        <FactorImportStatus
          key={admissionId}
          id={admissionId}
          onClose={() => onAdmission(undefined)}
        />
      )}
    </Panel>
  );
}
function FactorImportStatus({
  id,
  onClose,
}: {
  id: string;
  onClose: () => void;
}) {
  const [polling, setPolling] = useState(true);
  const admission = useQuery({
    ...factorImportOptions(id),
    refetchOnWindowFocus: polling,
    refetchOnReconnect: polling,
    refetchInterval: (query) =>
      polling && importStillRunning(query.state.data?.status) ? 1500 : false,
  });
  if (admission.isPending)
    return <Loading text="Reading exact import admission…" />;
  if (admission.error)
    return (
      <ErrorState
        error={admission.error}
        retry={() => void admission.refetch()}
      />
    );
  if (!admission.data) return null;
  const result = admission.data;
  if (result.id !== id)
    return (
      <ErrorState
        error={
          new Error("Import response does not match the selected admission.")
        }
      />
    );
  return (
    <section className="mt-5" aria-label="Exact import status">
      <Metadata
        values={{
          Admission: result.id,
          Status: result.status,
          "Archive SHA-256": result.archive_sha256,
          "Processing generation": result.generation,
          Envelope: result.result_envelope_id,
        }}
      />
      {importStillRunning(result.status) && (
        <>
          <p role="status">
            {result.status === "queued"
              ? "Queued for isolated verification. No imported snapshot is ready yet."
              : "Isolated verification is in progress. No success is inferred from elapsed time."}
          </p>
          <Button variant="outline" onClick={() => setPolling(!polling)}>
            {polling ? "Pause status checks" : "Resume status checks"}
          </Button>
        </>
      )}
      {result.status === "blocked" && (
        <p role="alert" className="notice error">
          Import blocked: {result.error_code || "recovery required"}.
          Containment or publication outcome is unresolved. No automatic
          resubmission or success claim is made.
        </p>
      )}
      {result.status === "failed" && (
        <p role="alert" className="notice error">
          Import failed: {result.error_code || "verification failed"}. Existing
          evidence is unchanged; this admission is not a ready factor snapshot.
        </p>
      )}
      {result.status === "succeeded" && (
        <>
          <p role="status">
            Import completed. Original identities are retained as source
            evidence; local snapshots are explicitly derived and source history
            remains unknown.
          </p>
          {result.local_bindings ? (
            <>
              <Link
                className="text-link"
                to="/factors/$factorId/versions/$versionId"
                params={{
                  factorId: result.local_bindings.factor_id,
                  versionId: result.local_bindings.version_id,
                }}
                search={{ tab: "evidence" }}
              >
                Open exact imported factor
              </Link>
              <ul>
                {result.local_bindings.snapshots.map((snapshot) => (
                  <li className="mt-2" key={snapshot.source_snapshot_id}>
                    {snapshot.snapshot_id ? (
                      <Link
                        className="text-link"
                        to="/factors/$factorId/versions/$versionId"
                        params={{
                          factorId: result.local_bindings!.factor_id,
                          versionId: result.local_bindings!.version_id,
                        }}
                        search={{
                          snapshot: snapshot.snapshot_id,
                          tab: "values",
                        }}
                      >
                        Open derived snapshot · {snapshot.snapshot_id}
                      </Link>
                    ) : (
                      `Source snapshot unresolved · ${snapshot.reason || snapshot.status}`
                    )}
                  </li>
                ))}
              </ul>
              <JsonDetails
                title="Preserved origin and exact local bindings"
                value={result.local_bindings}
              />
              <JsonDetails
                title="Unknown exposure history and provenance"
                value={result.exposure_history}
              />
            </>
          ) : (
            <ErrorState
              error={
                new Error(
                  "Completed admission has no local binding evidence. Do not infer a factor identity.",
                )
              }
            />
          )}
        </>
      )}
      <div className="mt-3">
        <Button variant="outline" onClick={onClose}>
          Close status view
        </Button>
      </div>
      <p className="muted">
        Closing or pausing this view does not cancel server processing. This
        exact admission can be reopened using its saved URL.
      </p>
    </section>
  );
}
