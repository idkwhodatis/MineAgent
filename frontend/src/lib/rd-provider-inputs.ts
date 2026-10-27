import { z } from "zod";
import { deploymentUrl } from "./deployment";
import type {
  EngineCapability,
  RdModelProfileOption,
} from "@/client/types.gen";
import type { EnginePreflight } from "./engine-runs-api";
export type GenerationMode = "fixture" | "configured";
export function generationCanCreate(
  mode: GenerationMode,
  capability: EngineCapability | undefined,
  model: RdModelProfileOption | undefined,
) {
  return mode === "fixture"
    ? capability?.can_create === true
    : capability?.configured_model_can_create === true &&
        model?.can_create === true;
}
export function preflightMatchesSelection(
  result: EnginePreflight | null,
  mode: GenerationMode,
  modelId: string,
  parent: string,
  steers: string[],
) {
  if (!result) return false;
  const request = result.normalized_request;
  if (mode === "fixture")
    return request.schema_version === "mineagent.rdagent-submission.v1";
  if (
    request.schema_version !== "mineagent.rdagent-provider-submission.v1" &&
    request.schema_version !== "mineagent.rdagent-provider-seeded-submission.v1"
  )
    return false;
  return (
    "model_profile_id" in request &&
    request.model_profile_id === modelId &&
    ("parent_factor_version_id" in request
      ? request.parent_factor_version_id
      : "") === parent &&
    JSON.stringify(request.selected_steer_version_ids ?? []) ===
      JSON.stringify(steers)
  );
}

export function providerPreflightSearch(search: Record<string, unknown>): {
  parent_factor_version_id?: string;
} {
  if (
    search.parent_factor_version_id === undefined ||
    search.parent_factor_version_id === ""
  )
    return {};
  return {
    parent_factor_version_id: z.uuid().parse(search.parent_factor_version_id),
  };
}
export function seededPreflightHref(versionId: string) {
  return deploymentUrl(
    `/engine-runs/preflight?parent_factor_version_id=${encodeURIComponent(z.uuid().parse(versionId))}`,
  );
}
