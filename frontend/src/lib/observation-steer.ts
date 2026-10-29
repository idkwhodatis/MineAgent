import { factorRequest } from "./factor-api";
import { readSession, watchSession } from "./session";
import type { SteerView } from "./rd-steers";
/** A user-reviewed evidence pointer, never result approval or an execution rule. */
export type ResearchObservation = {
  kind: string;
  refs: Record<string, string>;
  sessions?: string[];
};
export function observationContext(observation: ResearchObservation): string {
  if (!/^[a-z][a-z0-9_-]{0,63}$/.test(observation.kind))
    throw new Error("Unsupported observation kind.");
  const entries = Object.entries(observation.refs).sort(([a], [b]) =>
    a.localeCompare(b),
  );
  if (!entries.length || entries.length > 16)
    throw new Error("Observation identity is missing or too large.");
  for (const [key, value] of entries) {
    if (
      !/^[a-z][a-z0-9_]{0,63}$/.test(key) ||
      !value ||
      value.length > 128 ||
      /[\r\n\x00-\x1f]/.test(value)
    )
      throw new Error("Invalid observation identity.");
  }
  const sessions = observation.sessions ?? [];
  if (
    sessions.length > 32 ||
    new Set(sessions).size !== sessions.length ||
    sessions.some((value) => !/^\d{4}-\d{2}-\d{2}$/.test(value))
  )
    throw new Error(
      "Select at most 32 exact observed sessions without duplicates.",
    );
  const text = JSON.stringify(
    {
      kind: observation.kind,
      refs: Object.fromEntries(entries),
      ...(sessions.length ? { sessions: [...sessions].sort() } : {}),
    },
    null,
    2,
  );
  if (text.length > 1500)
    throw new Error(
      "Select a smaller observation context; no references were truncated.",
    );
  return text;
}
export function observationSteerText(
  guidance: string,
  context: string,
): string {
  if (!guidance.trim())
    throw new Error("Write your research guidance before saving.");
  const text = `${guidance.trim()}\n\nUser-selected observation context (evidence pointers, not approval or enforced constraints):\n${context}`;
  if (text.length > 2048)
    throw new Error(
      "Guidance and exact context exceed 2048 characters; shorten guidance or selection.",
    );
  return text;
}

export async function saveReviewedObservation(
  guidance: string,
  context: string,
  expectedToken?: string,
): Promise<SteerView> {
  if (!expectedToken || readSession()?.accessToken !== expectedToken)
    throw new Error(
      "Account changed; close this draft and reload under the current account.",
    );
  const text = observationSteerText(guidance, context);
  const saved = await factorRequest<SteerView>("/rd-steers", {
    method: "POST",
    body: { kind: "direction", text },
  });
  if (readSession()?.accessToken !== expectedToken)
    throw new Error(
      "Account changed; the previous save outcome is not shown in this account.",
    );
  if (saved.current.text !== text || saved.current.kind !== "direction")
    throw new Error(
      "Saved version does not match the reviewed guidance; inspect history.",
    );
  return saved;
}

export function watchObservationSession(
  expectedToken: string | undefined,
  revoke: () => void,
) {
  const check = () => {
    if (!expectedToken || readSession()?.accessToken !== expectedToken)
      revoke();
  };
  const stop = watchSession(check);
  check();
  return stop;
}
