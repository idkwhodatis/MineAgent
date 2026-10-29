import type {
  BudgetVector,
  ProviderStructuredPreflight,
} from "@/client/types.gen";

export function OEProviderPreflightResult({
  result,
  stale,
  locked = false,
  onApplyBudget,
}: {
  result: ProviderStructuredPreflight;
  stale: boolean;
  locked?: boolean;
  onApplyBudget: (budget: BudgetVector) => void;
}) {
  return (
    <section aria-label="Normal OpenEvolve preflight evidence">
      <h3>Server-checked fixed-snapshot search</h3>
      {stale && (
        <p role="alert">
          Declarations changed. This preflight cannot authorize the edited
          request; check eligibility again.
        </p>
      )}
      <p>
        Data eligible: {result.data_eligible ? "yes" : "no"}. Fresh submission
        eligible: {!stale && result.can_submit ? "yes" : "no"}. A preflight
        creates no research run.
      </p>
      <p>
        Data qualification: {result.data_qualification ?? "not established"}.
        Comparison:{" "}
        {result.qualification ?? "fixed_snapshot_comparison_not_causal_gain"}.
      </p>
      <ul>
        {result.reasons.map((reason) => (
          <li key={reason.code}>
            {reason.code}: {reason.message}
          </li>
        ))}
      </ul>
      <p>
        Required physical units (cumulative, not concurrent slots):{" "}
        {result.required_process_units}.
      </p>
      <p className="break-all">Request SHA-256: {result.request_sha256}</p>
      <p className="break-all">Preflight SHA-256: {result.preflight_sha256}</p>
      <p className="break-all">
        Fixed input SHA-256: {result.fixed_input_sha256 ?? "unavailable"}
      </p>
      <p className="break-all">
        Runtime profile SHA-256:{" "}
        {result.runtime_profile_sha256 ?? "unavailable"}
      </p>
      {result.model_binding && (
        <p>
          Bound operator profile {result.model_binding.profile_id}; model{" "}
          {result.model_binding.model};{" "}
          {result.model_binding.synthetic
            ? "synthetic transport"
            : "operator transport"}
          . Billing unknown.
        </p>
      )}
      <details>
        <summary>Exact normalized user declaration</summary>
        <pre>{JSON.stringify(result.normalized_request, null, 2)}</pre>
      </details>
      {result.required_budget ? (
        <>
          <details open>
            <summary>Server-required bounded budget</summary>
            <pre>{JSON.stringify(result.required_budget, null, 2)}</pre>
          </details>
          <button
            type="button"
            disabled={locked || stale}
            onClick={() => onApplyBudget({ ...result.required_budget! })}
          >
            Apply required budget to draft
          </button>
          <p>
            Applying changes the draft only. A new preflight is required; an
            unresolved submitted request is never rewritten.
          </p>
        </>
      ) : (
        <p>
          No required budget is available. Resolve the server reasons; do not
          invent a price or budget.
        </p>
      )}
    </section>
  );
}
