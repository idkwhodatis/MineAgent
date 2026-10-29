import type { ProviderStructuredSearchRequest } from "@/client/types.gen";

export const oeBudgetFields = [
  "calls",
  "tokens",
  "mutation_slots",
  "evaluator_executions",
  "wall_milliseconds",
  "storage_bytes",
  "process_slots",
  "cost_microunits",
] as const;

/** Edits declarations only; selection, preflight and execution remain server-owned. */
export function OEProviderParameters({
  request,
  disabled = false,
  onChange,
}: {
  request: ProviderStructuredSearchRequest;
  disabled?: boolean;
  onChange: (request: ProviderStructuredSearchRequest) => void;
}) {
  const number = (value: string) => (value === "" ? Number.NaN : Number(value));
  return (
    <fieldset disabled={disabled}>
      <legend>Closed structured strategy declarations</legend>
      <p>
        The model may propose a complete replacement of these four parameters,
        not arbitrary Python. A proposal is not a fill or proof of improved
        performance.
      </p>
      {(
        [
          [
            "threshold",
            "Factor eligibility threshold",
            undefined,
            undefined,
            "any",
          ],
          ["top_k", "Maximum ranked instruments", 1, 100, "1"],
          [
            "maximum_weight",
            "Maximum target weight per instrument",
            0,
            1,
            "any",
          ],
          ["maximum_gross_weight", "Maximum total target weight", 0, 1, "any"],
        ] as const
      ).map(([key, label, min, max, step]) => (
        <label key={key}>
          {label}
          <input
            type="number"
            required
            min={min}
            max={max}
            step={step}
            value={
              Number.isFinite(request.seed_parameters[key])
                ? request.seed_parameters[key]
                : ""
            }
            onChange={(event) =>
              onChange({
                ...request,
                seed_parameters: {
                  ...request.seed_parameters,
                  [key]: number(event.target.value),
                },
              })
            }
          />
        </label>
      ))}
      <p>
        Weights are targets. Actual orders, rejected orders and fills remain
        separate observed results.
      </p>
      <label>
        Execution window start (inclusive)
        <input
          type="date"
          required
          value={request.objective.window.start}
          onChange={(event) =>
            onChange({
              ...request,
              objective: {
                ...request.objective,
                window: {
                  ...request.objective.window,
                  start: event.target.value,
                },
              },
            })
          }
        />
      </label>
      <label>
        Execution window end (exclusive)
        <input
          type="date"
          required
          value={request.objective.window.end}
          onChange={(event) =>
            onChange({
              ...request,
              objective: {
                ...request.objective,
                window: {
                  ...request.objective.window,
                  end: event.target.value,
                },
              },
            })
          }
        />
      </label>
      <label>
        Initial cash
        <input
          type="number"
          required
          min="0"
          step="any"
          value={
            Number.isFinite(request.objective.initial_cash)
              ? request.objective.initial_cash
              : ""
          }
          onChange={(event) =>
            onChange({
              ...request,
              objective: {
                ...request.objective,
                initial_cash: number(event.target.value),
              },
            })
          }
        />
      </label>
      <label>
        Minimum closed trades (optional)
        <input
          type="number"
          min="0"
          max="1000000"
          step="1"
          value={request.objective.minimum_closed_trades ?? ""}
          onChange={(event) =>
            onChange({
              ...request,
              objective: {
                ...request.objective,
                minimum_closed_trades:
                  event.target.value === "" ? null : number(event.target.value),
              },
            })
          }
        />
      </label>
      <p>
        Declared objective: maximize total return percentage minus maximum
        drawdown percentage, in percentage points, using unrounded native
        accounting. Undefined results are ineligible; this is a fixed-window
        comparison, not causal or annualized gain.
      </p>
      <label>
        Maximum model mutations
        <input
          type="number"
          required
          min="1"
          max="2"
          step="1"
          value={request.maximum_mutation_slots}
          onChange={(event) =>
            onChange({
              ...request,
              maximum_mutation_slots: number(event.target.value),
            })
          }
        />
      </label>
      <fieldset>
        <legend>Explicit research budget</legend>
        <p>
          Limits are not a price quote. Zero limits do not authorize execution.
          Apply a server-required budget explicitly, then check eligibility
          again. Billing remains unknown.
        </p>
        {oeBudgetFields.map((key) => (
          <label key={key}>
            {key}
            <input
              type="number"
              required
              min="0"
              step="1"
              value={
                Number.isFinite(request.budget[key]) ? request.budget[key] : ""
              }
              onChange={(event) =>
                onChange({
                  ...request,
                  budget: {
                    ...request.budget,
                    [key]: number(event.target.value),
                  },
                })
              }
            />
          </label>
        ))}
      </fieldset>
    </fieldset>
  );
}
