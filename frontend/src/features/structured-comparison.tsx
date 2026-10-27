import { Panel } from "@/components/common";
import { deploymentUrl, fragmentHref } from "@/lib/deployment";
import {
  effectLabels,
  type ComparisonDisplay,
  type StructuredExecution,
} from "@/lib/structured-comparison";

/** Pure presentation. Its owner-scoped route must obtain BOTH committed
 * release receipts before supplying data; this component grants no authority. */
export function StructuredComparisonPanel({
  display,
  release,
}: {
  display: ComparisonDisplay;
  release: (executionId: string) => void;
}) {
  if (display.state === "unavailable")
    return (
      <Panel title="Structured execution comparison unavailable">
        <p>{display.reason}</p>
        <p>No comparison or execution is implied by installed UI code.</p>
      </Panel>
    );
  if (display.state === "withheld")
    return (
      <Panel title="Release both exact executions before comparing">
        <p>
          All values, counts and change flags are withheld. Release records the
          complete retained factor and market data exposure; it does not
          authorize new execution.
        </p>
        {[display.before, display.after].map((id, index) => (
          <button
            key={`${index}-${id}`}
            disabled={display.pending}
            onClick={() => release(id)}
          >
            Release {index === 0 ? "before" : "after"} execution {id}
          </button>
        ))}
        {display.error && <p role="alert">{display.error}</p>}
      </Panel>
    );
  const pair = display.comparison;
  return (
    <div>
      <Panel title="Observed closed-program comparison">
        <p>
          The initial maintained fixture compares close-return parameter
          versions (lookback 2 and 1), not arbitrary model-generated source.
          Read the exact retained definitions below. This is not a claim of
          financial improvement or arbitrary strategy causality.
        </p>
        <p>
          Same market and closed program. Native process IDs are not compared as
          economic changes. Target allocations are not evidence of submitted
          orders or fills.
        </p>
        <dl>
          {effectLabels.map(([key, label]) => (
            <div key={key}>
              <dt>{label}</dt>
              <dd>
                {pair[key] ? "Observed difference" : "No observed change"}
              </dd>
            </div>
          ))}
        </dl>
        <table>
          <caption>Actual factor row values and availability</caption>
          <thead>
            <tr>
              <th>Listing</th>
              <th>Before</th>
              <th>After</th>
              <th>Observed change</th>
            </tr>
          </thead>
          <tbody>
            {pair.values.map((row) => (
              <tr key={row.listing_id}>
                <td>{row.listing_id}</td>
                <td>
                  {row.before_value ?? "No value"} ({row.before_availability})
                </td>
                <td>
                  {row.after_value ?? "No value"} ({row.after_availability})
                </td>
                <td>{row.observed_change ? "Yes" : "No"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <StructuredExecutionPanel execution={pair.before} side="Before" />
      <StructuredExecutionPanel execution={pair.after} side="After" />
    </div>
  );
}
export function StructuredExecutionPanel({
  execution: item,
  side,
}: {
  execution: StructuredExecution;
  side: string;
}) {
  const anchor = (kind: string, key: string | number) =>
    `structured-${item.execution_id}-${kind}-${encodeURIComponent(key)}`;
  return (
    <Panel title={`${side} execution ${item.execution_id}`}>
      <p>
        <a
          href={deploymentUrl(
            `/factors/${item.factor_id}/versions/${item.factor_version_id}`,
          )}
        >
          Exact factor version {item.factor_version_id}
        </a>
      </p>
      {item.parent_factor_version_id ? (
        <p>
          <a
            href={deploymentUrl(
              `/factors/${item.factor_id}/versions/${item.parent_factor_version_id}`,
            )}
          >
            Recorded parent version {item.parent_factor_version_id}
          </a>
        </p>
      ) : (
        <p>
          No recorded parent factor version. A parameter difference does not
          establish lineage.
        </p>
      )}
      <p>
        Structured strategy version:{" "}
        <code>{item.structured_strategy_version_id}</code>
      </p>
      <a
        href={deploymentUrl(
          `/engine-runs/${item.execution_snapshot_run_id}/snapshots/${item.execution_snapshot_id}`,
        )}
      >
        Open exact execution snapshot {item.execution_snapshot_id}
      </a>
      <p>
        Lookback {item.factor_definition.lookback_sessions}; lag{" "}
        {item.factor_definition.lag_sessions ?? 0}; output unit{" "}
        {item.factor_definition.output_unit}.
      </p>
      <details>
        <summary>Retained definition and commitments</summary>
        <pre>{JSON.stringify(item.factor_definition, null, 2)}</pre>
        <dl>
          {Object.entries({
            code: item.factor_code_sha256,
            snapshot_input: item.snapshot_input_sha256,
            result: item.result_sha256,
            market: item.market_sha256,
            program: item.program_sha256,
          }).map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </details>
      {item.decisions.map((decision) => (
        <section
          key={decision.event_seq}
          id={anchor("decision", decision.event_seq)}
        >
          <h3>Decision {decision.event_seq}</h3>
          <p>
            Observed at {decision.observation.decision_time}; session{" "}
            {decision.observation.session_date};{" "}
            {decision.observation.warmup ? "warmup" : "not warmup"}.
          </p>
          <table>
            <caption>
              Actual snapshot reads (not a source-code access claim)
            </caption>
            <thead>
              <tr>
                <th>Native listing / factor listing</th>
                <th>Factor version / snapshot</th>
                <th>Availability</th>
                <th>Value</th>
                <th>Row hash</th>
              </tr>
            </thead>
            <tbody>
              {decision.reads.map((row) => (
                <tr key={row.native_listing_id}>
                  <td>
                    {row.native_listing_id} / {row.factor_listing_id}
                  </td>
                  <td>
                    {row.factor_version_id} / {row.snapshot_id}
                  </td>
                  <td>{row.availability}</td>
                  <td>{row.value ?? "No value"}</td>
                  <td>{row.row_sha256 ?? "Withheld or absent"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>Eligible: {decision.eligible.join(", ") || "None"}</p>
          <ol aria-label="Ranked scores">
            {decision.ranking.map((score) => (
              <li key={score.listing_id}>
                {score.listing_id}: {score.value}
              </li>
            ))}
          </ol>
          <ul aria-label="Target allocations, not fills">
            {decision.targets.map((target) => (
              <li key={target.listing_id}>
                {target.listing_id}: {target.value}
              </li>
            ))}
          </ul>
          <details>
            <summary>Stage commitments</summary>
            <pre>
              {JSON.stringify(
                {
                  observation: decision.observation_sha256,
                  eligibility: decision.eligibility_sha256,
                  scores: decision.score_sha256,
                  allocation: decision.allocation_sha256,
                  reply: decision.reply_sha256,
                },
                null,
                2,
              )}
            </pre>
          </details>
          <ul>
            {item.orders
              .filter((order) => order.decision_sequence === decision.event_seq)
              .map((order) => (
                <li key={order.native_order_id}>
                  <a
                    href={fragmentHref(
                      `#${anchor("order", order.native_order_id)}`,
                    )}
                  >
                    Observed order {order.native_order_id}
                  </a>
                </li>
              ))}
          </ul>
        </section>
      ))}
      <h3>Observed native orders</h3>
      {!item.orders.length && <p>No observed orders.</p>}
      {item.orders.map((order) => (
        <section
          key={order.native_order_id}
          id={anchor("order", order.native_order_id)}
        >
          <h4>{order.native_order_id}</h4>
          <a
            href={fragmentHref(
              `#${anchor("decision", order.decision_sequence)}`,
            )}
          >
            Back to decision {order.decision_sequence}
          </a>
          <p>
            Logical order {order.logical_order_id}; listing {order.listing_id};{" "}
            {order.side}; quantity {order.quantity}; filled{" "}
            {order.filled_quantity}; status {order.status}; commission{" "}
            {order.commission}.
          </p>
          <ul>
            {item.fills
              .filter(
                (fill) =>
                  fill.logical_order_id === order.logical_order_id &&
                  fill.native_order_id === order.native_order_id,
              )
              .map((fill) => (
                <li key={fill.native_fill_id}>
                  <a
                    href={fragmentHref(
                      `#${anchor("fill", fill.native_fill_id)}`,
                    )}
                  >
                    Observed fill {fill.native_fill_id}
                  </a>
                </li>
              ))}
          </ul>
        </section>
      ))}
      <h3>Observed native fills</h3>
      {!item.fills.length && (
        <p>No observed fills. Targets and orders do not imply fills.</p>
      )}
      {item.fills.map((fill) => (
        <section
          key={fill.native_fill_id}
          id={anchor("fill", fill.native_fill_id)}
        >
          <h4>{fill.native_fill_id}</h4>
          <a href={fragmentHref(`#${anchor("order", fill.native_order_id)}`)}>
            Back to observed order {fill.native_order_id}
          </a>
          <p>
            Logical order {fill.logical_order_id}; listing {fill.listing_id};{" "}
            {fill.side}; quantity {fill.quantity}; price {fill.price};
            commission {fill.commission}; session {fill.session_date}.
          </p>
        </section>
      ))}
    </Panel>
  );
}
