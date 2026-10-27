import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ErrorState, Loading, Panel } from "@/components/common";
import { factorRequest } from "@/lib/factor-api";
import { deploymentUrl } from "@/lib/deployment";
import { describeError } from "@/lib/api";
import {
  steerCapabilityOptions,
  steerListOptions,
  steerHistoryOptions,
  appendSteerSelection,
  moveSteerSelection,
} from "@/lib/rd-steers";
import type { SteerKind, SteerView } from "@/lib/rd-steers";

export const STEER_QUALIFICATION =
  "User-provided guidance for future runs, not verified model compliance. Natural-language constraints are prompt guidance, not enforced platform rules. Editing or disabling never changes an accepted run’s frozen input; cancel a run separately.";

function VersionHistory({ id }: { id: string }) {
  const query = useQuery(steerHistoryOptions(id));
  if (query.isPending) return <Loading />;
  if (query.error) return <ErrorState error={query.error} />;
  return (
    <ol aria-label="Immutable steer versions">
      {query.data.data.map((version) => (
        <li key={version.version_id}>
          <strong>
            Version {version.ordinal} · {version.kind}
          </strong>
          <p className="whitespace-pre-wrap">{version.text}</p>
          <p className="break-all">
            {version.version_id} · SHA-256 {version.text_sha256}
          </p>
        </li>
      ))}
    </ol>
  );
}

function SteerEditor({
  value,
  close,
}: {
  value?: SteerView;
  close: () => void;
}) {
  const client = useQueryClient();
  // Preserve the revision opened for editing even if a background refetch advances it.
  const [base] = useState(value);
  const [kind, setKind] = useState<SteerKind>(
    base?.current.kind ?? "direction",
  );
  const [text, setText] = useState(base?.current.text ?? "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();
  async function save() {
    if (pending) return;
    setPending(true);
    setError(undefined);
    try {
      await factorRequest<SteerView>(
        base ? `/rd-steers/${encodeURIComponent(base.id)}` : "/rd-steers",
        {
          method: base ? "PATCH" : "POST",
          body: {
            kind,
            text,
            ...(base ? { expected_revision: base.revision } : {}),
          },
        },
      );
      await client.invalidateQueries({ queryKey: ["rd-steers"] });
      close();
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <h2>{base ? "Add immutable version" : "Add user steer"}</h2>
      <label>
        Kind{" "}
        <select
          value={kind}
          onChange={(event) => setKind(event.target.value as SteerKind)}
          disabled={pending}
        >
          <option value="direction">Research direction</option>
          <option value="hypothesis">Hypothesis</option>
          <option value="constraints">Constraints (guidance)</option>
        </select>
      </label>
      <label>
        Guidance text{" "}
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          maxLength={2048}
          required
          disabled={pending}
        />
      </label>
      {error != null && (
        <>
          <ErrorState error={error} />
          <p>
            Refresh and inspect saved history before retrying an uncertain
            write. A stale edit is not merged automatically.
          </p>
        </>
      )}
      <button type="submit" disabled={pending || !text.trim()}>
        {pending ? "Saving…" : "Save version"}
      </button>
      <button type="button" disabled={pending} onClick={close}>
        Close editor
      </button>
    </form>
  );
}
function SteerRow({ value }: { value: SteerView }) {
  const client = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [history, setHistory] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();
  async function changeState() {
    if (pending) return;
    setPending(true);
    setError(undefined);
    try {
      await factorRequest(`/rd-steers/${encodeURIComponent(value.id)}/state`, {
        method: "POST",
        body: { expected_revision: value.revision, disabled: !value.disabled },
      });
      await client.invalidateQueries({ queryKey: ["rd-steers"] });
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }
  return (
    <article className="mb-6">
      <h3>
        {value.current.kind} · Version {value.current.ordinal} ·{" "}
        {value.disabled
          ? "Disabled for future selection"
          : "Available for future selection"}
      </h3>
      <p className="whitespace-pre-wrap">{value.current.text}</p>
      <p className="break-all">
        {value.current.version_id} · SHA-256 {value.current.text_sha256}
      </p>
      <button onClick={() => setEditing(!editing)} disabled={pending}>
        Edit as new version
      </button>
      <button onClick={() => void changeState()} disabled={pending}>
        {value.disabled
          ? "Enable future selection"
          : "Disable future selection"}
      </button>
      <button onClick={() => setHistory(!history)}>
        {history ? "Hide history" : "Show immutable history"}
      </button>
      {error != null && <ErrorState error={error} />}
      {editing && <SteerEditor value={value} close={() => setEditing(false)} />}
      {history && <VersionHistory id={value.id} />}
    </article>
  );
}
export function RDSteersPage() {
  const cap = useQuery(steerCapabilityOptions);
  const list = useQuery({
    ...steerListOptions,
    enabled: cap.data?.can_manage === true,
  });
  const [adding, setAdding] = useState(false);
  return (
    <main>
      <h1>RD agent steer inputs</h1>
      <p>{STEER_QUALIFICATION}</p>
      <a href={deploymentUrl("/engine-runs")}>Back to research history</a>
      {cap.isPending ? (
        <Loading />
      ) : cap.error ? (
        <ErrorState error={cap.error} />
      ) : !cap.data?.can_manage ? (
        <p>
          Persistent steer management is unavailable: {cap.data?.reason_code}.
          No run input has been changed.
        </p>
      ) : (
        <Panel title="User-provided steer inputs (pinned)">
          <p>
            Pinned first by origin, not approval. Agent-generated proposals
            remain separate in each run’s input history.
          </p>
          <button onClick={() => setAdding(!adding)}>Add steer</button>
          {adding && <SteerEditor close={() => setAdding(false)} />}
          {list.isPending ? (
            <Loading />
          ) : list.error ? (
            <ErrorState error={list.error} />
          ) : (
            <>
              {list.data.count === 0 && <p>No saved steer inputs.</p>}
              {list.data.data.map((value) => (
                <SteerRow key={value.id} value={value} />
              ))}
            </>
          )}
        </Panel>
      )}
    </main>
  );
}

// Only the genuine provider submission form may consume this ordered selection.
// Rendering it does not submit or grant permission; old fixture forms do not use it.
export function SteerVersionSelection({
  rows,
  selected,
  onChange,
}: {
  rows: SteerView[];
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const [error, setError] = useState("");
  return (
    <fieldset>
      <legend>User steer versions (explicit order)</legend>
      <p>{STEER_QUALIFICATION}</p>
      <a href={deploymentUrl("/rd-steers")}>Manage saved steer inputs</a>
      {rows
        .filter((row) => !row.disabled)
        .map((row) => (
          <label key={row.id}>
            <input
              type="checkbox"
              name="selected-steer-version"
              value={row.current.version_id}
              checked={selected.includes(row.current.version_id)}
              onChange={(event) => {
                try {
                  onChange(
                    event.target.checked
                      ? appendSteerSelection(selected, row.current.version_id)
                      : selected.filter((id) => id !== row.current.version_id),
                  );
                  setError("");
                } catch (err) {
                  setError(describeError(err).message);
                }
              }}
            />
            {row.current.kind} v{row.current.ordinal}: {row.current.text}
          </label>
        ))}
      <ol>
        {selected.map((id, index) => {
          const row = rows.find((value) => value.current.version_id === id);
          return (
            <li key={id}>
              {row && !row.disabled
                ? row.current.text
                : `Selected version changed or unavailable: ${id}. Remove it and select explicitly; it will not be silently replaced.`}
              <button
                type="button"
                disabled={index === 0}
                onClick={() =>
                  onChange(moveSteerSelection(selected, index, -1))
                }
              >
                Move up
              </button>
              <button
                type="button"
                disabled={index === selected.length - 1}
                onClick={() => onChange(moveSteerSelection(selected, index, 1))}
              >
                Move down
              </button>
              <button
                type="button"
                onClick={() =>
                  onChange(selected.filter((value) => value !== id))
                }
              >
                Remove selected version
              </button>
            </li>
          );
        })}
      </ol>
      {error && <p role="alert">{error}</p>}
    </fieldset>
  );
}
