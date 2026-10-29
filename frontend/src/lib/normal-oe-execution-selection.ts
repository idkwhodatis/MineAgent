import type { NormalExecutionRecord } from "@/client/types.gen";

/** URL selects owned metadata only. It never grants release or substitutes a candidate. */
export function normalOEExecutionSelection(
  search: string,
  records: NormalExecutionRecord[],
):
  | { kind: "none" | "note" | "unavailable" }
  | { kind: "execution"; candidateId: string } {
  const query = new URLSearchParams(search);
  // The note protocol has its own stricter complete-identity validator.
  if (query.has("normal_note_version")) return { kind: "note" };
  if (!query.has("execution_id"))
    return query.size ? { kind: "unavailable" } : { kind: "none" };
  const id = query.get("execution_id")!;
  if (
    query.size !== 1 ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id)
  )
    return { kind: "unavailable" };
  const matches = records.filter((row) => row.execution_id === id);
  if (
    matches.length !== 1 ||
    records.filter((row) => row.candidate_id === matches[0].candidate_id)
      .length !== 1
  )
    return { kind: "unavailable" };
  return { kind: "execution", candidateId: matches[0].candidate_id };
}

/** Explicit React identity: owner/run changes discard all selection and release UI state. */
export function normalOEResultScopeKey(ownerId: string, runId: string) {
  return JSON.stringify([ownerId, runId]);
}
