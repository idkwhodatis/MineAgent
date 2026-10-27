import { queryOptions } from "@tanstack/react-query";
import { factorRequest } from "./factor-api";
import type { ResearchInputs, ResearchInputItem } from "@/client/types.gen";
export const inputGroups = [
  "user_provided",
  "agent_generated",
  "unknown",
] as const;
export function groupInputs(items: ResearchInputItem[]) {
  // User input pinning is a deterministic presentation rule, not approval state.
  return inputGroups.map((origin) => ({
    origin,
    items: items
      .filter((item) => item.origin === origin)
      .sort(
        (a, b) =>
          a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id),
      ),
  }));
}
export const engineInputsOptions = (run: string, offset = 0) =>
  queryOptions({
    queryKey: ["engine-inputs", run, offset],
    queryFn: ({ signal }) =>
      factorRequest<ResearchInputs>(
        `/engine-runs/${encodeURIComponent(run)}/inputs?offset=${offset}`,
        { signal },
      ),
    retry: false,
  });
