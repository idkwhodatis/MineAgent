import type { StructuredWorkflowRequest } from "@/workflow-contracts/types.gen";

/** Early declaration checks; server admission remains authoritative. No budget mutation. */
export function assertStructuredWorkflowDeclaration(
  request: StructuredWorkflowRequest,
) {
  const signal = request.research.window;
  const execution = request.structured.objective.window;
  const date = (value: string) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return Number.NaN;
    const ms = Date.parse(`${value}T00:00:00Z`);
    return Number.isFinite(ms) &&
      new Date(ms).toISOString().slice(0, 10) === value
      ? ms
      : Number.NaN;
  };
  const start = date(signal.start),
    end = date(signal.end);
  if (!Number.isFinite(start) || end - start !== 86400000)
    throw new Error("Structured workflow requires exactly one RD signal day.");
  if (!(date(execution.start) <= start && end < date(execution.end)))
    throw new Error(
      "OE execution window must contain the RD signal and later execution.",
    );
  for (const key of [
    "calls",
    "tokens",
    "mutation_slots",
    "evaluator_executions",
    "wall_milliseconds",
    "storage_bytes",
    "process_slots",
    "cost_microunits",
  ] as const) {
    const minimum =
      (request.research.budget[key] ?? 0) * request.maximum_iterations +
      (request.structured.budget[key] ?? 0) +
      (key === "storage_bytes" ? 65536 * (request.maximum_iterations - 1) : 0);
    const ceiling = request.budget_ceiling[key] ?? 0;
    if (
      !Number.isSafeInteger(minimum) ||
      !Number.isSafeInteger(ceiling) ||
      ceiling < minimum ||
      minimum < 0
    )
      throw new Error(
        `Explicit total workflow budget ${key} must cover all RD iterations and the OE child.`,
      );
  }
}
