import type { QueryClient, QueryFilters } from "@tanstack/react-query";
import { completeFactorRead } from "./factor-api";

// These reads record exposures under the same owner lock as publication.
// Await the HTTP response; cancelling a browser read is not a server unlock.
const exposureReads = new Set([
  "list",
  "detail",
  "values",
  "sample",
  "comparison-values",
]);
export const factorExposureReads: QueryFilters = {
  predicate: ({ queryKey }) =>
    queryKey[0] === "factors" && exposureReads.has(String(queryKey[1])),
};
export function requireFactorReadsIdle(
  client: Pick<QueryClient, "isFetching">,
): void {
  if (client.isFetching(factorExposureReads) > 0)
    throw new Error(
      "Factor exposure recording is still in progress. Wait for it to finish, then submit explicitly.",
    );
}

/** Do not abort an older exposure read or mistake it for post-mutation data. */
export async function refreshFactorsAfterMutation(
  client: QueryClient,
): Promise<void> {
  await completeFactorRead(async (requireSameSession) => {
    const pending = client
      .getQueryCache()
      .findAll({ queryKey: ["factors"] })
      .filter((query) => query.state.fetchStatus === "fetching")
      .map((query) => query.promise);
    // These are earlier independent reads, not the mutation's success proof.
    // Keep their original error state; only wait for their actual completion.
    await Promise.allSettled(pending);
    requireSameSession();
    await client.invalidateQueries(
      { queryKey: ["factors"] },
      { cancelRefetch: false },
    );
  });
}
