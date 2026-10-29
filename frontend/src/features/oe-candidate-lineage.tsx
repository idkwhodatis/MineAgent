import type {
  NormalExecutionRecord,
  NormalExecutionView,
} from "@/client/types.gen";

export type OEObservation = {
  kind: string;
  refs: Record<string, string>;
  sessions?: string[];
};

/** Only already released, identity-verified results may reach this helper. */
export function normalObservation(value: NormalExecutionView): OEObservation {
  const item = value.execution;
  return {
    kind: "released_structured_oe_result",
    refs: {
      candidate_id: value.candidate_id,
      run_id: item.run_id,
      execution_id: item.execution_id,
      program_version_id: item.structured_strategy_version_id,
      snapshot_id: item.execution_snapshot_id,
      source_run_id: item.execution_snapshot_run_id,
      factor_id: item.factor_id,
      factor_version_id: item.factor_version_id,
      result_sha256: item.result_sha256,
      program_sha256: item.program_sha256,
      market_sha256: item.market_sha256,
      snapshot_input_sha256: item.snapshot_input_sha256,
    },
  };
}

/** A selected real fill binds the asset/time to its released execution. */
export function normalFillObservation(
  value: NormalExecutionView,
  fillId: string,
): OEObservation {
  const fill = value.execution.fills.find(
    (item) => item.native_fill_id === fillId,
  );
  if (!fill) throw new Error("Fill is not in this released execution.");
  const observation = normalObservation(value);
  return {
    ...observation,
    kind: "released_structured_oe_fill",
    refs: {
      ...observation.refs,
      listing_id: fill.listing_id,
      native_fill_id: fill.native_fill_id,
    },
    sessions: [fill.session_date],
  };
}

export function candidateLineage(records: NormalExecutionRecord[]) {
  if (records.length > 2000)
    return { error: "Candidate graph exceeds display bound", nodes: [] };
  const index = new Map<string, NormalExecutionRecord>();
  for (const row of records) {
    if (index.has(row.candidate_id))
      return {
        error: "Duplicate candidate identity; ancestry unavailable",
        nodes: [],
      };
    index.set(row.candidate_id, row);
  }
  const nodes: {
    row: NormalExecutionRecord;
    depth: number;
    missingParent: boolean;
  }[] = [];
  for (const row of records) {
    const visited = new Set([row.candidate_id]);
    let current = row,
      depth = 0;
    while (
      current.parent_candidate_id &&
      index.has(current.parent_candidate_id)
    ) {
      if (visited.has(current.parent_candidate_id))
        return {
          error: "Cyclic candidate ancestry; selection unavailable",
          nodes: [],
        };
      visited.add(current.parent_candidate_id);
      current = index.get(current.parent_candidate_id)!;
      depth += 1;
    }
    nodes.push({
      row,
      depth,
      missingParent:
        row.parent_candidate_id !== null && !index.has(row.parent_candidate_id),
    });
  }
  // Group actual siblings under their recorded parent, never merely indent a
  // global chronological list (which could visually attach a child to another root).
  const compare = (a: (typeof nodes)[number], b: (typeof nodes)[number]) =>
    a.row.ordinal - b.row.ordinal ||
    a.row.candidate_id.localeCompare(b.row.candidate_id);
  const children = new Map<string, typeof nodes>();
  const roots: typeof nodes = [];
  for (const node of nodes) {
    const parent = node.row.parent_candidate_id;
    if (parent === null || node.missingParent) roots.push(node);
    else children.set(parent, [...(children.get(parent) ?? []), node]);
  }
  const pending = roots.sort(compare).reverse();
  const ordered: typeof nodes = [];
  while (pending.length) {
    const node = pending.pop()!;
    ordered.push(node);
    pending.push(
      ...(children.get(node.row.candidate_id) ?? []).sort(compare).reverse(),
    );
  }
  return { error: null, nodes: ordered };
}

export function OECandidateLineage({
  records,
  selected,
  onSelect,
}: {
  records: NormalExecutionRecord[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const graph = candidateLineage(records);
  if (graph.error) return <p role="alert">{graph.error}</p>;
  return (
    <section aria-label="Structured candidate version lineage">
      <p>
        Structured strategy candidates, not native Python checkpoints. Edges are
        retained parent relationships, not proof of improvement.
      </p>
      <ol>
        {graph.nodes.map(({ row, depth, missingParent }) => (
          <li
            key={row.candidate_id}
            style={{
              marginInlineStart: `${Math.min(depth, 8)}rem`,
              borderInlineStart: "2px solid currentColor",
              paddingInlineStart: "0.75rem",
            }}
          >
            <p>
              Version ordinal {row.ordinal} · Candidate {row.candidate_id};
              origin {row.origin}; parent{" "}
              {row.parent_candidate_id ?? "none recorded"}; program{" "}
              {row.program_version_id}.
            </p>
            {missingParent && (
              <p role="status">
                Parent is not in the loaded records. This is not a root or an
                authorized comparison.
              </p>
            )}
            {depth > 8 && (
              <p>
                Recorded ancestry depth {depth}; indentation capped for
                readability.
              </p>
            )}
            {row.execution_id ? (
              <button
                type="button"
                aria-pressed={selected === row.candidate_id}
                onClick={() => onSelect(row.candidate_id)}
              >
                Inspect execution {row.execution_id}
              </button>
            ) : (
              <span>No execution recorded; no result is inferred.</span>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
