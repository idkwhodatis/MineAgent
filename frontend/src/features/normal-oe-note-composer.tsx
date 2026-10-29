import { useEffect, useRef, useState } from "react";
import type {
  NormalExecutionView,
  NormalOeNoteCapture,
  ResearchNoteRevision,
} from "@/client/types.gen";
import { ErrorState, Panel } from "@/components/common";
import { factorRequest } from "@/lib/factor-api";
import { readSession, watchSession } from "@/lib/session";
import { deploymentHref } from "@/lib/deployment";
import {
  loadNoteDraft,
  storeNoteDraft,
  clearNoteDraft,
  prepareNoteSubmission,
  type NoteDraft,
} from "@/lib/note-draft";
import { saveNoteDraft } from "@/lib/note-api";
import { stableJson } from "@/lib/data-utils";
import { safeNormalOENoteHref } from "@/lib/normal-oe-note-selection";

/** Explicitly capture a released original; reuse existing note revisions and retry keys. */
export function NormalOENoteComposer({
  ownerId,
  value,
}: {
  ownerId: string;
  value: NormalExecutionView;
}) {
  const execution = value.execution;
  const scope = `normal-oe:${execution.run_id}:${execution.execution_id}`;
  const [token] = useState(() => readSession()?.accessToken);
  const [revoked, setRevoked] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [invalid, setInvalid] = useState(false);
  const [draft, setDraft] = useState<NoteDraft | null>(null);
  const [selection, setSelection] = useState("execution");
  const [busy, setBusy] = useState(false);
  const [discardConfirmed, setDiscardConfirmed] = useState(false);
  const [saved, setSaved] = useState<ResearchNoteRevision | null>(null);
  const lock = useRef(false),
    active = useRef(true);
  useEffect(() => {
    active.current = true;
    try {
      setDraft(loadNoteDraft(ownerId, scope));
    } catch (failure) {
      setError(failure);
      setInvalid(true);
    }
    const unwatch = watchSession(() => {
      if (readSession()?.accessToken !== token) setRevoked(true);
    });
    return () => {
      active.current = false;
      unwatch();
    };
  }, [ownerId, scope, token]);
  const current = () =>
    active.current && !!token && readSession()?.accessToken === token;
  async function confirmOwner() {
    if (!current())
      throw new Error(
        "Session changed; original note request remains retained.",
      );
    const owner = await factorRequest<{ id: string }>("/users/me");
    if (!current() || owner.id !== ownerId)
      throw new Error("Original note owner differs; nothing is submitted.");
  }
  async function capture() {
    if (lock.current || draft || invalid || !current()) return;
    lock.current = true;
    setBusy(true);
    setError(null);
    try {
      await confirmOwner();
      const query = new URLSearchParams();
      if (selection === "execution") query.set("kind", "normal_oe_execution");
      else if (selection.startsWith("decision:")) {
        query.set("kind", "normal_oe_decision");
        query.set("decision_sequence", selection.slice(9));
      } else {
        query.set("kind", "normal_oe_fill");
        query.set("native_fill_id", selection.slice(5));
      }
      const captured = await factorRequest<NormalOeNoteCapture>(
        `/research-note-evidence/normal-oe/${encodeURIComponent(execution.run_id)}/${encodeURIComponent(execution.execution_id)}?${query}`,
      );
      if (!current()) return;
      if (
        captured.qualification !==
          "released_original_identity_not_note_write" ||
        captured.evidence.owner_id !== ownerId ||
        captured.evidence.run_id !== execution.run_id ||
        captured.evidence.execution_id !== execution.execution_id ||
        captured.evidence.candidate_id !== value.candidate_id ||
        captured.evidence.result_sha256 !== execution.result_sha256 ||
        !safeNormalOENoteHref(captured.href)
      )
        throw new Error(
          "Captured note identity differs from this released execution.",
        );
      const next: NoteDraft = {
        version: 1,
        note_id: null,
        base_revision: null,
        base_revision_sha256: null,
        key: null,
        fingerprint: null,
        change_note: "",
        content: {
          title: "Normal structured OE observation",
          body: "",
          status: "pending",
          tags: [],
          evidence: [captured.evidence],
          restore: {
            schema_version: "mineagent.note-normal-oe-restore.v1",
            evidence_index: 0,
          },
        },
      };
      storeNoteDraft(ownerId, scope, next);
      setDraft(next);
      setSaved(null);
    } catch (failure) {
      if (current()) setError(failure);
    } finally {
      lock.current = false;
      if (active.current) setBusy(false);
    }
  }
  function edit(field: "title" | "body", text: string) {
    if (!draft || draft.key || busy || !current()) return;
    const next = { ...draft, content: { ...draft.content, [field]: text } };
    try {
      storeNoteDraft(ownerId, scope, next);
      setDraft(next);
      setSaved(null);
    } catch (failure) {
      setError(failure);
    }
  }
  async function save() {
    if (!draft || lock.current || invalid || !current()) return;
    lock.current = true;
    setBusy(true);
    setError(null);
    try {
      await confirmOwner();
      const exact = prepareNoteSubmission(draft);
      storeNoteDraft(ownerId, scope, exact);
      setDraft(exact);
      const receipt = await saveNoteDraft(exact);
      if (!current()) return;
      if (
        (exact.note_id && receipt.note_id !== exact.note_id) ||
        receipt.revision !== (exact.base_revision ?? 0) + 1 ||
        stableJson(receipt.content.evidence) !==
          stableJson(exact.content.evidence)
      )
        throw new Error(
          "Note acknowledgement differs; retain the original key and body.",
        );
      const next: NoteDraft = {
        ...exact,
        note_id: receipt.note_id,
        base_revision: receipt.revision,
        base_revision_sha256: receipt.revision_sha256,
        content: receipt.content,
        key: null,
        fingerprint: null,
      };
      storeNoteDraft(ownerId, scope, next);
      setDraft(next);
      setSaved(receipt);
    } catch (failure) {
      if (current()) setError(failure);
    } finally {
      lock.current = false;
      if (active.current) setBusy(false);
    }
  }
  if (revoked || !token || readSession()?.accessToken !== token)
    return (
      <p>
        Note context unavailable after session change; sign in and reopen the
        exact execution.
      </p>
    );
  const link = saved && safeNormalOENoteHref(saved.restore_href);
  return (
    <Panel title="Pin a normal structured OE observation">
      <p>
        Human notes are not model approval or causal proof. Capture rechecks
        existing disclosure and original bytes; it never releases results.
      </p>
      <label>
        Normal OE note observation
        <select
          value={selection}
          disabled={busy || !!draft?.key || invalid}
          onChange={(event) => setSelection(event.target.value)}
        >
          <option value="execution">This exact execution</option>
          {execution.decisions.map((row) => (
            <option key={row.event_seq} value={`decision:${row.event_seq}`}>
              Actual decision {row.event_seq}
            </option>
          ))}
          {execution.fills.map((row) => (
            <option
              key={row.native_fill_id}
              value={`fill:${row.native_fill_id}`}
            >
              Normal structured fill {row.native_fill_id} · {row.session_date}
            </option>
          ))}
        </select>
      </label>
      <button
        disabled={busy || !!draft || invalid}
        onClick={() => void capture()}
      >
        Capture exact released note evidence
      </button>
      {draft && (
        <section aria-label="Exact normal OE note draft">
          <p>
            {draft.note_id
              ? `Editing note ${draft.note_id}, base revision ${draft.base_revision}.`
              : "New human note; not saved yet."}
          </p>
          <label>
            Normal note title
            <input
              value={draft.content.title}
              maxLength={240}
              disabled={busy || !!draft.key}
              onChange={(event) => edit("title", event.target.value)}
            />
          </label>
          <label>
            Normal note body
            <textarea
              value={draft.content.body ?? ""}
              maxLength={32000}
              disabled={busy || !!draft.key}
              onChange={(event) => edit("body", event.target.value)}
            />
          </label>
          <pre>{JSON.stringify(draft.content.evidence, null, 2)}</pre>
          <button
            disabled={busy || invalid || !!saved}
            onClick={() => void save()}
          >
            {draft.key
              ? "Retry exact normal note request"
              : "Save exact normal note revision"}
          </button>
          {draft.key && (
            <p>
              Unresolved original key/body retained; editing and replacement
              capture are locked.
            </p>
          )}
        </section>
      )}
      {draft && !draft.key && (
        <div>
          <label>
            <input
              type="checkbox"
              checked={discardConfirmed}
              onChange={(event) => setDiscardConfirmed(event.target.checked)}
            />
            Discard only this local editor draft; saved server notes remain
            unchanged.
          </label>
          <button
            disabled={busy || !discardConfirmed}
            onClick={() => {
              try {
                clearNoteDraft(ownerId, scope);
                setDraft(null);
                setSaved(null);
                setDiscardConfirmed(false);
              } catch (failure) {
                setError(failure);
              }
            }}
          >
            Clear local draft for a separate note
          </button>
        </div>
      )}
      {saved && (
        <p role="status">
          Saved note {saved.note_id} revision {saved.revision}.{" "}
          {link && (
            <a href={deploymentHref(link)}>
              Restore this exact admitted observation
            </a>
          )}
        </p>
      )}
      {error != null && <ErrorState error={error} />}
    </Panel>
  );
}
