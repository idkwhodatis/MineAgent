/** Draft strict normal-only link parser; not wired into legacy note restoration. */
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const sha = /^[0-9a-f]{64}$/;
export function safeNormalOENoteHref(
  href: string | null | undefined,
): string | null {
  if (!href || !href.startsWith("/") || href.startsWith("//")) return null;
  try {
    const url = new URL(href, "https://normal-notes.invalid");
    const match =
      /^\/engine-runs\/structured-search\/runs\/([0-9a-f-]{36})$/.exec(
        url.pathname,
      );
    if (
      url.origin !== "https://normal-notes.invalid" ||
      url.hash ||
      !match ||
      !uuid.test(match[1])
    )
      return null;
    const pairs = [...url.searchParams.entries()];
    if (new Set(pairs.map(([key]) => key)).size !== pairs.length) return null;
    const values = Object.fromEntries(pairs);
    const keys = [
      "normal_note_version",
      "execution_id",
      "candidate_id",
      "program_version_id",
      "result_sha256",
      "evidence_sha256",
      "kind",
    ];
    if (
      values.normal_note_version !== "1" ||
      !["execution_id", "candidate_id", "program_version_id"].every((key) =>
        uuid.test(values[key] ?? ""),
      ) ||
      !["result_sha256", "evidence_sha256"].every((key) =>
        sha.test(values[key] ?? ""),
      )
    )
      return null;
    if (values.kind === "normal_oe_decision") {
      keys.push("decision_sequence", "row_sha256");
      if (
        !/^[0-7]$/.test(values.decision_sequence ?? "") ||
        !sha.test(values.row_sha256 ?? "")
      )
        return null;
    } else if (values.kind === "normal_oe_fill") {
      keys.push("native_fill_id", "listing_id", "session_date", "row_sha256");
      if (
        !values.native_fill_id ||
        values.native_fill_id.length > 200 ||
        /[\x00-\x1f\x7f]/.test(values.native_fill_id) ||
        !uuid.test(values.listing_id ?? "") ||
        !sha.test(values.row_sha256 ?? "") ||
        !/^\d{4}-\d{2}-\d{2}$/.test(values.session_date ?? "")
      )
        return null;
      const date = new Date(`${values.session_date}T00:00:00Z`);
      if (
        !Number.isFinite(date.getTime()) ||
        date.toISOString().slice(0, 10) !== values.session_date
      )
        return null;
    } else if (values.kind !== "normal_oe_execution") return null;
    if (
      keys.length !== pairs.length ||
      pairs.some(([key]) => !keys.includes(key))
    )
      return null;
    return `${url.pathname}${url.search}`;
  } catch {
    return null;
  }
}

/** Pure draft selector. Both values must come from separately verified reads.
 * `admittedHref` is the exact available note-evidence resolution, not caller URL.
 * Row-byte/hash verification belongs to the future server resolver; this function
 * only locates the unique row in that same admitted result hash, not rehashing it.
 * Returning an anchor never releases data or claims the UI has restored it.
 */
export function normalOENoteAnchor(
  requestedHref: string,
  admittedHref: string | null | undefined,
  record: import("@/client/types.gen").NormalExecutionRecord,
  released: import("@/client/types.gen").NormalExecutionView,
):
  | { kind: "execution" }
  | { kind: "decision"; sequence: number }
  | { kind: "fill"; id: string }
  | null {
  const safe = safeNormalOENoteHref(requestedHref);
  if (!safe || safeNormalOENoteHref(admittedHref) !== safe) return null;
  const url = new URL(safe, "https://normal-notes.invalid");
  const q = url.searchParams;
  const execution = released.execution;
  if (
    url.pathname !==
      `/engine-runs/structured-search/runs/${execution.run_id}` ||
    q.get("execution_id") !== record.execution_id ||
    q.get("execution_id") !== execution.execution_id ||
    q.get("candidate_id") !== record.candidate_id ||
    q.get("candidate_id") !== released.candidate_id ||
    q.get("program_version_id") !== record.program_version_id ||
    q.get("program_version_id") !== execution.structured_strategy_version_id ||
    q.get("result_sha256") !== execution.result_sha256 ||
    record.snapshot_id !== execution.execution_snapshot_id ||
    record.source_run_id !== execution.execution_snapshot_run_id
  )
    return null;
  if (q.get("kind") === "normal_oe_execution") return { kind: "execution" };
  if (q.get("kind") === "normal_oe_decision") {
    const sequence = Number(q.get("decision_sequence"));
    return execution.decisions.filter((row) => row.event_seq === sequence)
      .length === 1
      ? { kind: "decision", sequence }
      : null;
  }
  const matches = execution.fills.filter(
    (row) =>
      row.native_fill_id === q.get("native_fill_id") &&
      row.listing_id === q.get("listing_id") &&
      row.session_date === q.get("session_date"),
  );
  return matches.length === 1
    ? { kind: "fill", id: matches[0].native_fill_id }
    : null;
}
