/** Actual generated027 SDK; shared authenticated/subpath transport configured by api. */
import "./api";
import * as sdk from "@/client/sdk.gen";
import type {
  ProviderStructuredSearchRequest,
  ProviderStructuredSearchSubmit,
} from "@/client/types.gen";
import { readSession } from "./session";

async function reply<T>(
  call: () => Promise<{ data?: T; error?: unknown; response?: Response }>,
): Promise<T> {
  const token = readSession()?.accessToken;
  const result = await call();
  if (readSession()?.accessToken !== token)
    throw new Error("Session changed; reload owned search inputs.");
  if (!result.response)
    throw new Error(
      "The search service could not be reached; retain the original submission for retry.",
    );
  if (!result.response.ok || result.error !== undefined) {
    const error =
      result.error && typeof result.error === "object" ? result.error : {};
    throw { ...error, status: result.response.status };
  }
  if (result.data === undefined) throw new Error("Search response is missing.");
  return result.data;
}
export const oeCapabilities = (signal?: AbortSignal) =>
  reply(() => sdk.getOeStructuredCapabilities({ signal, throwOnError: false }));
export const oeModels = (signal?: AbortSignal) =>
  reply(() =>
    sdk.listOeStructuredModelProfiles({ signal, throwOnError: false }),
  );
export const oeSnapshots = (offset: number, signal?: AbortSignal) =>
  reply(() =>
    sdk.listOeStructuredSnapshots({
      query: { offset, limit: 20 },
      signal,
      throwOnError: false,
    }),
  );
export const oePreflight = (body: ProviderStructuredSearchRequest) =>
  reply(() => sdk.preflightOeStructuredSearch({ body, throwOnError: false }));
export const oeSubmit = (body: ProviderStructuredSearchSubmit, key: string) =>
  reply(() =>
    sdk.submitOeStructuredSearch({
      body,
      headers: { "Idempotency-Key": key },
      throwOnError: false,
    }),
  );
export const oeRun = (run: string, signal?: AbortSignal) =>
  reply(() =>
    sdk.getOeStructuredRun({
      path: { run_id: run },
      signal,
      throwOnError: false,
    }),
  );

export { reply as oeReply };
export const oeCancel = (run: string) =>
  reply(() =>
    sdk.cancelOeStructuredRun({ path: { run_id: run }, throwOnError: false }),
  );
