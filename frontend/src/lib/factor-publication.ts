import type { QueryClient, QueryFilters } from "@tanstack/react-query";

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
