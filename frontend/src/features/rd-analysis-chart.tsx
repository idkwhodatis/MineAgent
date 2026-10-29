import { useState } from "react";
import type { DirectAnalysisResult, DirectDay } from "@/client/types.gen";

export type RDAnalysisObservation = {
  kind: string;
  refs: Record<string, string>;
  sessions?: string[];
};
type Metric = "ic" | "rank_ic";
const WIDTH = 720;
const HEIGHT = 180;
const X = 42;
const Y = 16;
const xAt = (index: number, count: number) =>
  X + (count === 1 ? 0.5 : index / Math.max(1, count - 1)) * (WIDTH - X - 16);
const validCorrelation = (value: number | null): value is number =>
  value !== null && Number.isFinite(value) && value >= -1 && value <= 1;
const yAt = (value: number) => Y + ((1 - value) / 2) * (HEIGHT - Y * 2);

/** A null observation ends a segment; missing correlations are never zero-filled. */
export function correlationSegments(days: DirectDay[], metric: Metric) {
  const segments: string[] = [];
  let points: string[] = [];
  days.forEach((day, index) => {
    const value = day[metric];
    if (validCorrelation(value)) {
      points.push(`${xAt(index, days.length)},${yAt(value)}`);
    } else if (points.length) {
      segments.push(points.join(" "));
      points = [];
    }
  });
  if (points.length) segments.push(points.join(" "));
  return segments;
}

export function analysisObservation(
  analysis: DirectAnalysisResult,
  selectedSessions: readonly string[],
): RDAnalysisObservation {
  const wanted = new Set(selectedSessions);
  const sessions = analysis.result.days
    .filter((day) => wanted.has(day.session))
    .map((day) => day.session);
  if (sessions.length > 32)
    throw new Error(
      "Select no more than 32 retained sessions for guidance context.",
    );
  return {
    kind: "released_rd_direct_analysis",
    refs: {
      run_id: analysis.run_id,
      analysis_id: analysis.analysis_id,
      execution_snapshot_id: analysis.execution_snapshot_id,
      result_sha256: analysis.result_sha256,
      measurement_sha256: analysis.measurement_sha256,
      protocol_sha256: analysis.result.protocol_sha256,
    },
    sessions,
  };
}

/** Reject a retained draft as soon as the loaded immutable result changes. */
export function observationForAnalysis(
  observation: RDAnalysisObservation | undefined,
  analysis: DirectAnalysisResult,
): RDAnalysisObservation | undefined {
  if (!observation) return undefined;
  const expected = analysisObservation(analysis, []);
  return observation.kind === expected.kind &&
    Object.entries(expected.refs).every(
      ([key, value]) => observation.refs[key] === value,
    )
    ? observation
    : undefined;
}

export function toggleAnalysisSession(previous: string[], session: string) {
  return previous.includes(session)
    ? previous.filter((value) => value !== session)
    : previous.length < 32
      ? [...previous, session]
      : previous;
}

function CorrelationChart({
  days,
  metric,
  selected,
  onSelect,
}: {
  days: DirectDay[];
  metric: Metric;
  selected: ReadonlySet<string>;
  onSelect?: (session: string) => void;
}) {
  const label = metric === "ic" ? "Daily IC" : "Daily rank IC";
  return (
    <figure>
      <figcaption>
        {label}: correlation, −1 to 1. Gaps are undefined observations.
      </figcaption>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT + 24}`}
        role={onSelect ? "group" : "img"}
        aria-label={label}
        style={{ width: "100%", maxWidth: WIDTH }}
      >
        <title>
          {label}; exact session values and undefined reasons are in the table
          below.
        </title>
        {[-1, 0, 1].map((value) => (
          <g key={value}>
            <line
              x1={X}
              x2={WIDTH - 16}
              y1={yAt(value)}
              y2={yAt(value)}
              stroke="currentColor"
              opacity="0.18"
            />
            <text x="2" y={yAt(value) + 4} fontSize="12" fill="currentColor">
              {value}
            </text>
          </g>
        ))}
        {correlationSegments(days, metric).map((points, index) => (
          <polyline
            key={index}
            points={points}
            fill="none"
            stroke={metric === "ic" ? "#2563eb" : "#9333ea"}
            strokeWidth="2"
          />
        ))}
        {days.map(
          (day, index) =>
            validCorrelation(day[metric]) && (
              <circle
                key={day.session}
                cx={xAt(index, days.length)}
                cy={yAt(day[metric])}
                r={selected.has(day.session) ? 6 : 4}
                fill={
                  selected.has(day.session)
                    ? "#b45309"
                    : metric === "ic"
                      ? "#2563eb"
                      : "#9333ea"
                }
                role={onSelect ? "button" : undefined}
                tabIndex={onSelect ? 0 : undefined}
                aria-label={
                  onSelect
                    ? `Select ${day.session}: ${label} ${day[metric]}`
                    : undefined
                }
                aria-pressed={onSelect ? selected.has(day.session) : undefined}
                onClick={onSelect ? () => onSelect(day.session) : undefined}
                onKeyDown={
                  onSelect
                    ? (event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          onSelect(day.session);
                        }
                      }
                    : undefined
                }
              >
                <title>
                  {day.session}: {label} {day[metric]}
                </title>
              </circle>
            ),
        )}
        <text x={X} y={HEIGHT + 16} fontSize="12" fill="currentColor">
          {days[0]?.session}
        </text>
        <text
          x={WIDTH - 16}
          y={HEIGHT + 16}
          textAnchor="end"
          fontSize="12"
          fill="currentColor"
        >
          {days.at(-1)?.session}
        </text>
      </svg>
    </figure>
  );
}

export function RDAnalysisChart({
  analysis,
  onObservation,
}: {
  analysis: DirectAnalysisResult;
  onObservation?: (observation: RDAnalysisObservation) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const days = analysis.result.days;
  const maximumPairs = days.reduce(
    (maximum, day) => Math.max(maximum, day.pair_count),
    1,
  );
  const selectedSet = new Set(selected);
  function toggle(session: string) {
    setSelected((previous) => toggleAnalysisSession(previous, session));
  }
  return (
    <section aria-label="Retained daily factor diagnostics">
      <p>
        Factor/forward-label cross-sectional correlations, not strategy returns
        or a net asset value curve.
      </p>
      <p>
        Coverage is reported as observed paired counts. Expected-universe
        coverage percentage is unavailable; no denominator is inferred.
      </p>
      <p>
        Positions follow retained session order; chart spacing does not imply
        equal calendar-day intervals.
      </p>
      <p>
        Group-label spread: not computed for this execution. No retained
        released group diagnostic is available.
      </p>
      {!days.length ? (
        <p>No retained daily observations.</p>
      ) : (
        <>
          <CorrelationChart
            days={days}
            metric="ic"
            selected={selectedSet}
            onSelect={onObservation ? toggle : undefined}
          />
          <CorrelationChart
            days={days}
            metric="rank_ic"
            selected={selectedSet}
            onSelect={onObservation ? toggle : undefined}
          />
          <figure>
            <figcaption>
              Observed factor/label pairs per session (count, not a coverage
              percentage).
            </figcaption>
            <svg
              viewBox={`0 0 ${WIDTH} ${HEIGHT + 24}`}
              role="img"
              aria-label="Daily paired observations"
              style={{ width: "100%", maxWidth: WIDTH }}
            >
              <title>
                Observed pair counts; exact values are in the table below.
              </title>
              <line
                x1={X}
                x2={WIDTH - 16}
                y1={HEIGHT - Y}
                y2={HEIGHT - Y}
                stroke="currentColor"
              />
              <text x="2" y="16" fontSize="12" fill="currentColor">
                {maximumPairs}
              </text>
              <text x="2" y={HEIGHT - Y} fontSize="12" fill="currentColor">
                0
              </text>
              {days.map((day, index) => (
                <line
                  key={day.session}
                  x1={xAt(index, days.length)}
                  x2={xAt(index, days.length)}
                  y1={HEIGHT - Y}
                  y2={
                    HEIGHT -
                    Y -
                    (day.pair_count / maximumPairs) * (HEIGHT - 2 * Y)
                  }
                  stroke="#0f766e"
                  strokeWidth={Math.min(
                    12,
                    (WIDTH - X - 16) / Math.max(1, days.length),
                  )}
                >
                  <title>
                    {day.session}: {day.pair_count} pairs
                  </title>
                </line>
              ))}
            </svg>
          </figure>
          <div className="table-wrap">
            <table>
              <caption>Exact retained daily observations</caption>
              <thead>
                <tr>
                  {onObservation && <th>Guidance context</th>}
                  <th>Session</th>
                  <th>IC</th>
                  <th>Rank IC</th>
                  <th>Paired observations</th>
                  <th>Defined state</th>
                </tr>
              </thead>
              <tbody>
                {days.map((day) => (
                  <tr key={day.session}>
                    {onObservation && (
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Use session ${day.session} as guidance context`}
                          checked={selectedSet.has(day.session)}
                          disabled={
                            !selectedSet.has(day.session) &&
                            selected.length >= 32
                          }
                          onChange={() => toggle(day.session)}
                        />
                      </td>
                    )}
                    <td>{day.session}</td>
                    <td>{validCorrelation(day.ic) ? day.ic : "Not defined"}</td>
                    <td>
                      {validCorrelation(day.rank_ic)
                        ? day.rank_ic
                        : "Not defined"}
                    </td>
                    <td>{day.pair_count}</td>
                    <td>{day.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {onObservation && (
        <>
          <p>
            Select up to 32 retained sessions for explicit guidance review. This
            does not select assets, change data windows, apply guidance or rerun
            an experiment.
          </p>
          <button
            type="button"
            disabled={!selected.length}
            onClick={() =>
              onObservation(analysisObservation(analysis, selected))
            }
          >
            Prepare guidance from selected sessions
          </button>
        </>
      )}
    </section>
  );
}
