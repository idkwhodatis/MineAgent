import { useRef, useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearch,
} from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ErrorState, Loading, PageHeader, Panel } from "@/components/common";
import {
  Field,
  JsonDetails,
  Metadata,
  Pagination,
} from "@/components/data-common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { currentUserOptions } from "@/lib/api";
import {
  factorCapabilitiesOptions,
  factorDetailOptions,
  factorListOptions,
  factorPresentationOptions,
  factorRequest,
  factorVersionsOptions,
  type FactorDetail,
  type FactorPresentation,
} from "@/lib/factor-api";
import { FactorImportPanel } from "./import";
import { FactorExecutions } from "./executions";
import { FactorStructuredExecutions } from "../structured-results";
import { ExecutionPicker, ExecutionTabContent } from "./execution-tabs";
import { FactorEditor } from "./editor";
import {
  factorSearch,
  factorTabs,
  selectFactorSnapshot,
  selectFactorExecution,
  selectFactorVersion,
  type FactorSelection,
} from "./selection";
import {
  FactorValuesPanel,
  FactorSnapshotEvidence,
  FactorEvaluationPanel,
  FactorUsagePanel,
  FactorEvolutionPanel,
  FactorExportPanel,
} from "./panels";
import "./workbench.css";
const tabLabels = {
  overview: "Overview & calculation",
  values: "Values",
  evaluation: "Research evaluation",
  evolution: "Evolution",
  usage: "Strategy usage",
  evidence: "Code & evidence",
};

export function FactorLibraryPage() {
  const capability = useQuery(factorCapabilitiesOptions);
  return (
    <>
      <PageHeader
        title="Factor library"
        subtitle="Independent definitions, actual calculation evidence, and exact reusable versions. No strategy run is required."
      />
      {capability.isPending ? (
        <Loading />
      ) : capability.error ? (
        <ErrorState
          error={capability.error}
          retry={() => void capability.refetch()}
        />
      ) : !capability.data?.enabled ? (
        <p className="notice">
          The factor library is disabled for this installation. Existing
          strategy research remains available.
        </p>
      ) : (
        <FactorLibrary importEnabled={capability.data.package_import_enabled} />
      )}
    </>
  );
}
function FactorLibrary({ importEnabled }: { importEnabled: boolean }) {
  const librarySelection = useSearch({ from: "/workspace/factors" });
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [bookmarked, setBookmarked] = useState(false);
  const [editing, setEditing] = useState(false);
  const [importActive, setImportActive] = useState(false);
  const user = useQuery(currentUserOptions);
  const list = useQuery({
    ...factorListOptions(search, offset, bookmarked),
    enabled: !editing && !importActive,
    refetchOnWindowFocus: !editing && !importActive,
    refetchOnReconnect: !editing && !importActive,
  });
  const navigate = useNavigate();
  return (
    <div className="factor-workbench">
      <div className="factor-toolbar">
        <Field id="factor-search" label="Search factors">
          <Input
            id="factor-search"
            maxLength={240}
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setOffset(0);
            }}
          />
        </Field>
        <label>
          <input
            type="checkbox"
            checked={bookmarked}
            onChange={(event) => {
              setBookmarked(event.target.checked);
              setOffset(0);
            }}
          />{" "}
          Bookmarked only
        </label>
        <Button
          onClick={() => setEditing(true)}
          disabled={!user.data || list.isFetching}
        >
          New factor definition
        </Button>
      </div>
      {list.isFetching && (
        <p role="status">
          Recording displayed factor exposures. Creation and import wait for
          this read to finish.
        </p>
      )}
      {editing && user.data && (
        <FactorEditor
          owner={user.data.id}
          onClose={() => setEditing(false)}
          onSaved={(factorId, versionId) => {
            setEditing(false);
            void navigate({
              to: "/factors/$factorId/versions/$versionId",
              params: { factorId, versionId },
              search: {},
            });
          }}
        />
      )}
      <Panel title="Saved factors">
        {list.isPending ? (
          <Loading />
        ) : list.error ? (
          <ErrorState error={list.error} retry={() => void list.refetch()} />
        ) : (
          list.data && (
            <>
              {!list.data.count ? (
                <p className="factor-empty">
                  No matching factors. Save a definition independently of any
                  strategy or research engine.
                </p>
              ) : (
                <div className="factor-table-wrap">
                  <table className="factor-table">
                    <thead>
                      <tr>
                        <th>Name / version</th>
                        <th>Type</th>
                        <th>Calculation</th>
                        <th>Coverage</th>
                        <th>Evaluation</th>
                        <th>Usage evidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {list.data.data.map((item) => (
                        <tr key={item.id}>
                          <td>
                            <Link
                              className="text-link"
                              to="/factors/$factorId/versions/$versionId"
                              params={{
                                factorId: item.id,
                                versionId: item.latest_version_id,
                              }}
                              search={{
                                ...(item.latest_execution &&
                                item.latest_execution.factor_id === item.id &&
                                item.latest_execution.factor_version_id ===
                                  item.latest_version_id
                                  ? {
                                      execution:
                                        item.latest_execution
                                          .execution_snapshot_id,
                                    }
                                  : {
                                      snapshot:
                                        item.selected_snapshot_id || undefined,
                                    }),
                              }}
                            >
                              {item.name} · v{item.ordinal}
                            </Link>
                            {item.bookmarked && (
                              <span aria-label="Bookmarked"> ★</span>
                            )}
                          </td>
                          <td>{item.kind.replaceAll("_", " ")}</td>
                          <td>
                            {item.latest_execution
                              ? item.latest_execution.publication_state ===
                                "completed"
                                ? "Execution publication recorded"
                                : "Execution publication pending"
                              : item.calculation_state.replaceAll("_", " ")}
                          </td>
                          <td>
                            {item.coverage === null
                              ? item.latest_execution
                                ? "Execution coverage withheld — open exact version to release"
                                : "No legacy snapshot"
                              : `${item.coverage.valid_count}/${item.coverage.row_count} valid · ${item.coverage.missing_count} missing`}
                          </td>
                          <td>
                            {item.latest_execution
                              ? item.latest_execution.analysis_state ===
                                "result_recorded"
                                ? "Direct result recorded; measurement withheld"
                                : item.latest_execution.analysis_state ===
                                    "pending"
                                  ? "Direct analysis pending"
                                  : "No direct analysis declared"
                              : item.analysis_status.replaceAll("_", " ")}
                          </td>
                          <td>
                            {item.usage.declared} declared ·{" "}
                            {item.usage.explicit_read} explicit reads ·{" "}
                            {item.usage.experiment} experiments
                            <br />
                            <small>
                              {item.usage.observation_coverage.replaceAll(
                                "_",
                                " ",
                              )}
                            </small>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <Pagination
                count={list.data.count}
                offset={offset}
                limit={25}
                busy={list.isFetching}
                onPage={setOffset}
              />
            </>
          )
        )}
      </Panel>
      <FactorImportPanel
        enabled={importEnabled}
        onActiveChange={setImportActive}
        owner={user.data?.id}
        admissionId={librarySelection.import_id}
        onAdmission={(import_id) =>
          void navigate({ to: "/factors", search: { import_id } })
        }
      />
    </div>
  );
}
export function FactorVersionPage() {
  const { factorId, versionId } = useParams({
    from: "/workspace/factors/$factorId/versions/$versionId",
  });
  const selection = factorSearch(
    useSearch({ from: "/workspace/factors/$factorId/versions/$versionId" }),
  );
  const capability = useQuery(factorCapabilitiesOptions);
  return (
    <>
      <PageHeader
        title="Factor research workbench"
        subtitle="Read an exact definition and physical snapshot. Explanations, observations, and controlled evidence remain distinct."
      />
      {capability.isPending ? (
        <Loading />
      ) : capability.error ? (
        <ErrorState error={capability.error} />
      ) : !capability.data?.enabled ? (
        <p className="notice">
          The factor library is disabled for this installation.
        </p>
      ) : (
        <FactorWorkbench
          key={`${factorId}:${versionId}`}
          factorId={factorId}
          versionId={versionId}
          selection={selection}
        />
      )}
    </>
  );
}
function FactorWorkbench({
  factorId,
  versionId,
  selection,
}: {
  factorId: string;
  versionId: string;
  selection: FactorSelection;
}) {
  const [editing, setEditing] = useState(false);
  const detail = useQuery({
    ...factorDetailOptions(factorId, versionId),
    enabled: !editing,
    refetchOnWindowFocus: !editing,
    refetchOnReconnect: !editing,
  });
  const [versionOffset, setVersionOffset] = useState(0);
  const versions = useQuery(factorVersionsOptions(factorId, versionOffset));
  const user = useQuery(currentUserOptions);
  const navigate = useNavigate();
  const tab = selection.tab || "overview";
  const changeSelection = (next: FactorSelection) =>
    void navigate({
      to: "/factors/$factorId/versions/$versionId",
      params: { factorId, versionId },
      search: next,
    });
  if (detail.isPending) return <Loading />;
  if (detail.error)
    return (
      <ErrorState error={detail.error} retry={() => void detail.refetch()} />
    );
  if (!detail.data) return null;
  const data = detail.data;
  if (data.version.id !== versionId || data.version.factor_id !== factorId)
    return (
      <ErrorState
        error={
          new Error(
            "The response does not match the selected immutable version.",
          )
        }
      />
    );
  const snapshot = data.snapshots.find(
    (item) => item.id === selection.snapshot,
  );
  const snapshotProblem = Boolean(
    selection.snapshot &&
    (!snapshot ||
      snapshot.factor_version_id !== versionId ||
      snapshot.definition_sha256 !== data.version.definition_sha256),
  );
  return (
    <div className="factor-workbench">
      <div className="factor-toolbar">
        <Link to="/factors" className="text-link">
          All factors
        </Link>
        <Field id="factor-version" label="Exact factor version">
          <select
            id="factor-version"
            value={versionId}
            onChange={(e) =>
              void navigate({
                to: "/factors/$factorId/versions/$versionId",
                params: { factorId, versionId: e.target.value },
                search: selectFactorVersion(selection),
              })
            }
          >
            {versions.data ? (
              versions.data.data.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.ordinal} · {v.source_kind} · {v.id.slice(0, 8)}
                </option>
              ))
            ) : (
              <option value={versionId}>v{data.version.ordinal}</option>
            )}
            {versions.data &&
              !versions.data.data.some((v) => v.id === versionId) && (
                <option value={versionId}>
                  v{data.version.ordinal} · selected exact version
                </option>
              )}
          </select>
        </Field>
        <Field id="factor-snapshot" label="Exact physical snapshot">
          <p className="muted">
            Legacy provider snapshots only; research execution snapshots are
            selected below.
          </p>
          <select
            id="factor-snapshot"
            value={selection.snapshot || ""}
            onChange={(e) =>
              changeSelection(
                selectFactorSnapshot(selection, e.target.value || undefined),
              )
            }
          >
            <option value="">Definition only</option>
            {snapshotProblem && (
              <option value={selection.snapshot}>
                Unavailable selected snapshot
              </option>
            )}
            {data.snapshots.map((item) => (
              <option key={item.id} value={item.id}>
                {item.id.slice(0, 8)} · {item.valid_count}/{item.row_count}{" "}
                valid · {item.dataset_version_id.slice(0, 8)}
              </option>
            ))}
          </select>
        </Field>
        <Button
          variant="outline"
          onClick={() => setEditing(true)}
          disabled={!user.data}
        >
          Create child version
        </Button>
      </div>
      {versions.data && versions.data.count > 100 && (
        <Pagination
          offset={versionOffset}
          limit={100}
          count={versions.data.count}
          busy={versions.isFetching}
          onPage={setVersionOffset}
        />
      )}
      {versions.error && (
        <ErrorState
          error={versions.error}
          retry={() => void versions.refetch()}
        />
      )}
      {snapshotProblem && (
        <ErrorState
          error={
            new Error(
              "Selected snapshot is unavailable or belongs to a different definition. No substitute snapshot has been chosen.",
            )
          }
        />
      )}
      {detail.isFetching && (
        <p role="status">
          Recording displayed factor exposures. Creation and import wait for
          this read to finish.
        </p>
      )}
      {editing && user.data && (
        <FactorEditor
          owner={user.data.id}
          parent={data.version}
          onClose={() => setEditing(false)}
          onSaved={(nextFactor, nextVersion) =>
            void navigate({
              to: "/factors/$factorId/versions/$versionId",
              params: { factorId: nextFactor, versionId: nextVersion },
              search: selectFactorVersion(selection),
            })
          }
        />
      )}
      <nav className="factor-tabs" aria-label="Factor views">
        {factorTabs.map((value) => (
          <Link
            key={value}
            to="/factors/$factorId/versions/$versionId"
            params={{ factorId, versionId }}
            search={{ ...selection, tab: value }}
            aria-current={tab === value ? "page" : undefined}
          >
            {tabLabels[value]}
          </Link>
        ))}
      </nav>
      <ExecutionPicker
        key={versionId}
        factorId={factorId}
        versionId={versionId}
        value={selection.execution}
        select={(id) => changeSelection(selectFactorExecution(selection, id))}
      />
      <ExecutionTabContent
        version={data.version}
        selection={selection}
        versions={versions.data?.data || []}
        changeSelection={changeSelection}
      />
      {tab === "overview" && <FactorOverview detail={data} />}
      {(tab === "overview" || tab === "values") &&
        !selection.execution &&
        !selection.invalidExecution &&
        !snapshotProblem &&
        (snapshot ? (
          <FactorValuesPanel
            key={snapshot.id}
            snapshot={snapshot}
            selection={selection}
            onSelection={changeSelection}
            compact={tab === "overview"}
          />
        ) : (
          <Panel title="Calculation evidence">
            <p className="factor-empty">
              {data.snapshots.length
                ? "Choose an exact physical snapshot to inspect actual values and calculation samples."
                : "No materialization has been published for this version. Saving code alone does not produce calculation evidence."}
            </p>
          </Panel>
        ))}
      {tab === "evaluation" &&
        !selection.execution &&
        !selection.invalidExecution && <FactorEvaluationPanel detail={data} />}
      {tab === "usage" && <FactorUsagePanel usage={data.usage} />}
      {tab === "evolution" && (
        <FactorEvolutionPanel
          detail={data}
          versions={versions.data?.data || []}
          snapshot={
            snapshotProblem || selection.execution || selection.invalidExecution
              ? undefined
              : snapshot
          }
          owner={user.data?.id}
        />
      )}
      {tab === "evidence" && (
        <>
          <Panel title="Exact code and identity">
            <Metadata
              values={{
                Version: data.version.id,
                "Definition SHA-256": data.version.definition_sha256,
                "Source SHA-256": data.version.code_sha256,
                "Source kind": data.version.source_kind,
                Created: data.version.created_at,
              }}
            />
            <pre
              className="factor-code"
              tabIndex={0}
              data-testid="factor-source"
            >
              {data.version.code}
            </pre>
            <JsonDetails
              title="Immutable definition"
              value={data.version.definition}
            />
            <JsonDetails
              title="Source provenance (not a verification claim)"
              value={data.version.source_provenance}
            />
          </Panel>
          {snapshot && !snapshotProblem && (
            <FactorSnapshotEvidence snapshot={snapshot} />
          )}
          {user.data && (
            <FactorExportPanel
              version={data.version}
              snapshot={
                snapshotProblem ||
                selection.execution ||
                selection.invalidExecution
                  ? undefined
                  : snapshot
              }
              owner={user.data.id}
            />
          )}
        </>
      )}
      <FactorExecutions
        key={versionId}
        factorId={factorId}
        versionId={versionId}
      />
      <FactorStructuredExecutions
        key={versionId}
        factorId={factorId}
        versionId={versionId}
      />
      <FactorPresentationEditor factorId={factorId} />
    </div>
  );
}
function FactorOverview({ detail }: { detail: FactorDetail }) {
  const definition = detail.version.definition;
  return (
    <Panel title={`Version ${detail.version.ordinal} · independent factor`}>
      <div className="factor-grid">
        <article>
          <h3>What it computes</h3>
          <p>
            {String(definition.factor_kind).replaceAll("_", " ")} ·{" "}
            {String(definition.frequency)} · output in{" "}
            {String(definition.output_unit)}
          </p>
          <p>{String(definition.interpretation).replaceAll("_", " ")}</p>
          <p className="muted">
            Definition metadata, not an inferred formula or proof that arbitrary
            source was executed.
          </p>
        </article>
        <article>
          <h3>What changed</h3>
          <p>
            {detail.version.parent_version_id
              ? "A child of an exact prior version. Open Evolution for bilateral source, definition and value evidence."
              : "Root definition. No parent comparison is applicable."}
          </p>
        </article>
        <article>
          <h3>Observed calculation</h3>
          <p>
            {detail.snapshots.length} legacy published physical snapshot
            {detail.snapshots.length === 1 ? "" : "s"}.
          </p>
          <p>
            Lookback: {String(definition.lookback_sessions)} sessions. Lag:{" "}
            {String(definition.lag_sessions)} sessions.
          </p>
          <p>
            Calculation universe:{" "}
            {String(definition.calculation_universe_policy).replaceAll(
              "_",
              " ",
            )}
          </p>
        </article>
        <article>
          <h3>Evidence source</h3>
          <p>{detail.version.source_kind.replaceAll("_", " ")}</p>
          <p>
            Research evaluation: not evaluated unless an exact recorded analysis
            is shown.
          </p>
          <p>
            Reading or computing a factor does not prove causal contribution to
            a strategy.
          </p>
        </article>
      </div>
      <JsonDetails
        title="Exact input fields and preprocessing"
        value={{
          input_fields: definition.input_fields,
          preprocessing: definition.preprocessing,
          parameters: definition.parameters,
        }}
      />
    </Panel>
  );
}
function FactorPresentationEditor({ factorId }: { factorId: string }) {
  // Presentation edits never carry or mutate definition/source fields.
  const presentation = useQuery(factorPresentationOptions(factorId));
  const factor = presentation.data;
  const [name, setName] = useState("");
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const active = useRef(false);
  const queries = useQueryClient();
  async function patch(body: Partial<FactorPresentation>) {
    if (!factor || active.current) return;
    active.current = true;
    setBusy(true);
    setError(undefined);
    try {
      await factorRequest(`/factors/${factorId}`, {
        method: "PATCH",
        body: {
          expected_presentation_revision: factor.presentation_revision,
          ...body,
        },
      });
      await queries.invalidateQueries({ queryKey: ["factors"] });
    } catch (err) {
      setError(err);
    } finally {
      active.current = false;
      setBusy(false);
    }
  }
  if (presentation.error)
    return (
      <ErrorState
        error={presentation.error}
        retry={() => void presentation.refetch()}
      />
    );
  if (!factor) return null;
  return (
    <Panel title="Presentation only">
      <div className="factor-toolbar">
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => void patch({ bookmarked: !factor.bookmarked })}
        >
          {factor.bookmarked ? "Remove bookmark" : "Bookmark factor"}
        </Button>
        <Field id="factor-display-name" label="Display name">
          <Input
            id="factor-display-name"
            placeholder={factor.name}
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={240}
          />
        </Field>
        <Button
          disabled={busy || !name.trim()}
          onClick={() => void patch({ name })}
        >
          Rename factor
        </Button>
      </div>
      <p className="muted">
        Name and bookmark updates do not alter any immutable definition or
        snapshot.
      </p>
      {error !== undefined && <ErrorState error={error} />}
    </Panel>
  );
}
