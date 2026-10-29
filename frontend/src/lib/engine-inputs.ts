import { queryOptions } from "@tanstack/react-query";
import { factorRequest } from "./factor-api";
import type {
  ResearchInputs,
  ResearchInputItem,
  ResolvedInputVersion,
} from "@/client/types.gen";
export const inputGroups = [
  "user_provided",
  "workflow_stage",
  "agent_generated",
  "unknown",
] as const;
export function groupInputs(items: ResearchInputItem[]) {
  // User input pinning is a deterministic presentation rule, not approval state.
  return inputGroups
    .filter(
      (origin) =>
        origin !== "workflow_stage" ||
        items.some((item) => item.origin === origin),
    )
    .map((origin) => ({
      origin,
      items: items
        .filter((item) => item.origin === origin)
        .sort(
          (a, b) =>
            a.created_at.localeCompare(b.created_at) ||
            a.id.localeCompare(b.id),
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

export function groupWorkflowInputs(inputs: ResolvedInputVersion[]) {
  const positions = inputs.map((input, index) => ({
    input,
    position: index + 1,
  }));
  return (
    [
      "manual_user_submission",
      "normalized_external_proposal",
      "experiment_feedback_proposal",
      "unknown",
    ] as const
  ).map((origin) => ({
    origin,
    items: positions.filter(({ input }) => input.ref.origin === origin),
  }));
}
