export function researchConfig(value: unknown): boolean {
  return (
    value !== null &&
    typeof value === "object" &&
    "schema_version" in value &&
    value.schema_version === "tradegrad.run.v1"
  );
}

export function runDetailRoute(config: unknown) {
  // New engine specifications carry their own identity. Even an unknown pair
  // without a schema must never inherit legacy seed behavior.
  if (
    config &&
    typeof config === "object" &&
    ("engine" in config || "purpose" in config)
  )
    return "/engine-runs/$runId" as const;
  if (researchConfig(config)) return "/research-runs/$runId" as const;
  if (
    config &&
    typeof config === "object" &&
    !Array.isArray(config) &&
    !("schema_version" in config)
  )
    return "/runs/$runId" as const;
  // Unsupported identities reach an explicit owner/family-checked read, never seed semantics.
  return "/engine-runs/$runId" as const;
}

export function comparisonSearch(value: unknown): string {
  if (typeof value !== "string" || value.length > 147) return "";
  const ids = value.split(",");
  const uuid =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (ids.length > 4 || ids.some((id) => !uuid.test(id))) return "";
  return [...new Set(ids.map((id) => id.toLowerCase()))].join(",");
}

export function engineHistorySearch(search: Record<string, unknown>) {
  const offset = Number(search.offset ?? 0);
  return {
    offset:
      Number.isSafeInteger(offset) && offset >= 0 && offset <= 1_000_000
        ? offset
        : 0,
  };
}
