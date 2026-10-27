/** Generated025 contracts; display state is never a disclosure or launch grant. */
import type {
  StructuredExecutionView,
  StructuredPairComparison,
} from "@/client/types.gen";
export type StructuredExecution = StructuredExecutionView;
export type StructuredComparison = StructuredPairComparison;
export type ComparisonDisplay =
  | { state: "unavailable"; reason: string }
  | {
      state: "withheld";
      before: string;
      after: string;
      pending: boolean;
      error?: string;
    }
  | { state: "released"; comparison: StructuredComparison };
export const effectLabels = [
  ["eligibility_changed", "Eligibility"],
  ["score_values_changed", "Score values"],
  ["ranking_changed", "Ranking"],
  ["targets_changed", "Targets (not fills)"],
  ["observed_orders_changed", "Observed order economics"],
  ["observed_fills_changed", "Observed fill economics"],
] as const;
