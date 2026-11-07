import type { QueryClient, QueryKey } from "@tanstack/react-query";

const evidenceKinds = new Set([
  "history",
  "execution-snapshot",
  "values",
  "analysis",
]);

function belongsToRun(key: QueryKey, runId: string) {
  return (
    (key[0] === "engine-inputs" && key[1] === runId) ||
    (key[0] === "engine-history" &&
      typeof key[1] === "string" &&
      evidenceKinds.has(key[1]) &&
      key[2] === runId)
  );
}

/** Final metadata can commit between the independent detail/history polls.
 * Cancel even an initial in-flight read before invalidating: invalidation alone
 * can reuse that older request when it has no cached data yet. Inactive pages
 * become stale without eagerly fetching or releasing historical results.
 */
export async function refreshTerminalEngineEvidence(
  client: QueryClient,
  runId: string,
) {
  const filters = {
    predicate: (query: { queryKey: QueryKey }) =>
      belongsToRun(query.queryKey, runId),
  };
  await client.cancelQueries(filters);
  await client.invalidateQueries(filters);
}
