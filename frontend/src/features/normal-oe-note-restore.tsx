import { readSession, watchSession } from "@/lib/session";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type {
  NormalExecutionRecord,
  NormalOeNoteCapture,
} from "@/client/types.gen";
import { ErrorState, Loading, Panel } from "@/components/common";
import { APP_SUBPATH } from "@/lib/deployment";
import { factorRequest } from "@/lib/factor-api";
import { exactNormalResult, oeResult } from "@/lib/oe-results";
import {
  normalOENoteAnchor,
  safeNormalOENoteHref,
} from "@/lib/normal-oe-note-selection";

export function NormalOENoteRestoration({
  ownerId,
  runId,
  records,
  onSelected,
}: {
  ownerId: string;
  runId: string;
  records: NormalExecutionRecord[];
  onSelected: (candidate: string) => void;
}) {
  const [token] = useState(() => readSession()?.accessToken);
  const [revoked, setRevoked] = useState(false);
  useEffect(
    () =>
      watchSession(() => {
        if (readSession()?.accessToken !== token) setRevoked(true);
      }),
    [token],
  );
  const location = () => {
    if (
      typeof window === "undefined" ||
      !new URLSearchParams(window.location.search).has("normal_note_version")
    )
      return null;
    const path = window.location.pathname;
    return `${APP_SUBPATH && path.startsWith(APP_SUBPATH + "/") ? path.slice(APP_SUBPATH.length) : path}${window.location.search}`;
  };
  const [requested, setRequested] = useState(location);
  useEffect(() => {
    const changed = () => setRequested(location());
    window.addEventListener("popstate", changed);
    return () => window.removeEventListener("popstate", changed);
  }, []);
  const safe = safeNormalOENoteHref(requested);
  const requestedQuery = safe
    ? new URL(safe, "https://normal-notes.invalid").searchParams
    : null;
  const matches = records.filter(
    (row) =>
      row.candidate_id === requestedQuery?.get("candidate_id") &&
      row.execution_id === requestedQuery?.get("execution_id"),
  );
  const record = matches.length === 1 ? matches[0] : undefined;
  const loaded = useQuery({
    queryKey: [
      "normal-oe-note-restore",
      ownerId,
      runId,
      safe,
      record?.execution_id,
    ],
    enabled: !revoked && !!token && !!safe && !!record?.execution_id,
    retry: false,
    queryFn: async ({ signal }) => {
      const q = new URLSearchParams({ kind: requestedQuery!.get("kind")! });
      for (const key of ["decision_sequence", "native_fill_id"]) {
        const value = requestedQuery!.get(key);
        if (value !== null) q.set(key, value);
      }
      const captured = await factorRequest<NormalOeNoteCapture>(
        `/research-note-evidence/normal-oe/${encodeURIComponent(runId)}/${encodeURIComponent(record!.execution_id!)}?${q}`,
        { signal },
      );
      if (
        captured.qualification !==
          "released_original_identity_not_note_write" ||
        captured.evidence.owner_id !== ownerId ||
        captured.href !== safe
      )
        throw new Error(
          "Exact note evidence unavailable: owner, original identity or row commitment changed.",
        );
      const result = exactNormalResult(
        await oeResult(runId, record!.execution_id!, signal),
        runId,
        record!,
      );
      const anchor = normalOENoteAnchor(safe!, captured.href, record!, result);
      if (readSession()?.accessToken !== token)
        throw new Error(
          "Session changed; original note selection is unavailable.",
        );
      if (!anchor)
        throw new Error(
          "Exact note row unavailable; no first or newer candidate was substituted.",
        );
      return { result, anchor, candidate: record!.candidate_id };
    },
  });
  useEffect(() => {
    if (
      !revoked &&
      token &&
      readSession()?.accessToken === token &&
      loaded.data &&
      !loaded.error &&
      !loaded.isFetching
    )
      onSelected(loaded.data.candidate);
  }, [
    loaded.data,
    loaded.error,
    loaded.isFetching,
    onSelected,
    revoked,
    token,
  ]);
  if (!requested) return null;
  if (revoked || !token || readSession()?.accessToken !== token)
    return (
      <p role="alert">
        Exact normal note selection is unavailable after session change.
      </p>
    );
  if (!safe || !record)
    return (
      <p role="alert">
        Exact normal note selection is unavailable or outside the loaded owned
        records. No candidate was substituted.
      </p>
    );
  if (loaded.isPending || loaded.isFetching) return <Loading />;
  if (loaded.error) return <ErrorState error={loaded.error} />;
  const { result, anchor } = loaded.data;
  const row =
    anchor.kind === "decision"
      ? result.execution.decisions.find(
          (item) => item.event_seq === anchor.sequence,
        )
      : anchor.kind === "fill"
        ? result.execution.fills.find(
            (item) => item.native_fill_id === anchor.id,
          )
        : null;
  return (
    <Panel title="Restored exact normal OE note observation">
      <p>
        Server rechecked existing release and original bytes. Selected execution{" "}
        {result.execution.execution_id};{" "}
        {anchor.kind === "execution"
          ? "whole execution"
          : anchor.kind === "decision"
            ? `decision ${anchor.sequence}`
            : `normal structured fill ${anchor.id}`}
        . This selection did not release results or change an experiment.
      </p>
      {row && <pre>{JSON.stringify(row, null, 2)}</pre>}
    </Panel>
  );
}
