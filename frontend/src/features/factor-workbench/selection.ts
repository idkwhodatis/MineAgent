export const factorTabs = [
  "overview",
  "values",
  "evaluation",
  "evolution",
  "usage",
  "evidence",
] as const;
export type FactorTab = (typeof factorTabs)[number];
export type FactorSelection = {
  tab?: FactorTab;
  snapshot?: string;
  execution?: string;
  compareVersion?: string;
  compareExecution?: string;
  invalidExecution?: boolean;
  listing?: string;
  date?: string;
  offset?: number;
};
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function factorSearch(input: Record<string, unknown>): FactorSelection {
  const offset = Number(input.offset);
  return {
    tab: factorTabs.includes(input.tab as FactorTab)
      ? (input.tab as FactorTab)
      : undefined,
    snapshot:
      typeof input.snapshot === "string" && uuid.test(input.snapshot)
        ? input.snapshot
        : undefined,
    execution:
      typeof input.execution === "string" && uuid.test(input.execution)
        ? input.execution
        : undefined,
    compareVersion:
      typeof input.compareVersion === "string" &&
      uuid.test(input.compareVersion)
        ? input.compareVersion
        : undefined,
    compareExecution:
      typeof input.compareExecution === "string" &&
      uuid.test(input.compareExecution)
        ? input.compareExecution
        : undefined,
    invalidExecution:
      input.invalidExecution === true ||
      ["execution", "compareVersion", "compareExecution"].some(
        (key) =>
          input[key] !== undefined &&
          (typeof input[key] !== "string" || !uuid.test(input[key] as string)),
      ) ||
      (!!input.execution && !!input.snapshot) ||
      undefined,
    listing:
      typeof input.listing === "string" &&
      input.listing.length <= 200 &&
      !/[\u0000-\u001f]/.test(input.listing)
        ? input.listing
        : undefined,
    date:
      typeof input.date === "string" &&
      /^\d{4}-\d{2}-\d{2}$/.test(input.date) &&
      !Number.isNaN(Date.parse(input.date)) &&
      new Date(input.date).toISOString().slice(0, 10) === input.date
        ? input.date
        : undefined,
    offset:
      Number.isSafeInteger(offset) && offset > 0 && offset <= 1_000_000
        ? offset
        : undefined,
  };
}
// Changing identity drops dependent cursors; history navigation restores the full URL.
export function selectFactorSnapshot(
  selection: FactorSelection,
  snapshot?: string,
): FactorSelection {
  return { tab: selection.tab, snapshot };
}
export function selectFactorVersion(
  selection: FactorSelection,
): FactorSelection {
  return { tab: selection.tab };
}
export function factorValue(
  value: number | null,
  state: string,
  reason?: string | null,
): string {
  if (state !== "valid")
    return reason ? `Unavailable · ${reason}` : `Unavailable · ${state}`;
  return value !== null && Number.isFinite(value)
    ? String(value)
    : "Invalid value evidence";
}
export function submissionKey(
  owner: string,
  operation: string,
  payload: unknown,
): string {
  const name = `mineagent.factor.submission.v1.${owner}.${operation}`;
  const fingerprint = JSON.stringify(payload);
  try {
    const previous = JSON.parse(sessionStorage.getItem(name) || "null") as {
      fingerprint: string;
      key: string;
    } | null;
    if (previous?.fingerprint === fingerprint && uuid.test(previous.key))
      return previous.key;
  } catch {
    /* A malformed pending draft cannot authorize a replay. */
  }
  const key = crypto.randomUUID();
  sessionStorage.setItem(name, JSON.stringify({ fingerprint, key }));
  return key;
}

export function factorLibrarySearch(input: Record<string, unknown>): {
  import_id?: string;
} {
  return {
    import_id:
      typeof input.import_id === "string" && uuid.test(input.import_id)
        ? input.import_id
        : undefined,
  };
}
export const MAX_FACTOR_IMPORT_BYTES = 10_000_000;
export async function factorArchiveHash(file: Blob): Promise<string> {
  if (file.size < 1 || file.size > MAX_FACTOR_IMPORT_BYTES)
    throw new Error(
      "Choose a nonempty raw ZIP package no larger than 10,000,000 bytes. The server may enforce a lower limit.",
    );
  return Array.from(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", await file.arrayBuffer()),
    ),
  )
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}
export const importStillRunning = (status?: string) =>
  status === "queued" || status === "processing";

// Only clear after the server has confirmed success. An uncertain response must
// retain its key; a later deliberate new generation must not reuse an old event.
export function forgetFactorSubmission(owner: string, operation: string): void {
  sessionStorage.removeItem(
    `mineagent.factor.submission.v1.${owner}.${operation}`,
  );
}

export function selectFactorExecution(
  selection: FactorSelection,
  execution?: string,
): FactorSelection {
  return { tab: selection.tab, execution };
}
