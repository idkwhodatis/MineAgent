import { useQuery } from "@tanstack/react-query";
import { ErrorState } from "@/components/common";
import type { ProviderStructuredSearchRequest } from "@/client/types.gen";
import { oeModelsOptions } from "@/lib/oe-provider-entry";
import { OEProviderParameters } from "./oe-provider-parameters";
import { OEProviderSelection } from "./oe-provider-selection";

/** A future OE declaration, never a fabricated released snapshot or launch. */
export function WorkflowStructuredChoice({
  ownerId,
  request,
  onChange,
}: {
  ownerId: string;
  request: ProviderStructuredSearchRequest;
  onChange: (value: ProviderStructuredSearchRequest) => void;
}) {
  const models = useQuery(oeModelsOptions(ownerId));
  const model = models.data?.find(
    (value) => value.binding.profile_id === request.model_profile_id,
  );
  return (
    <section aria-label="Terminal structured OE declaration">
      <p>
        After the final successful RD checkpoint, this plan waits for explicit
        snapshot and analysis release. No future snapshot is invented and this
        choice does not release any output. The OE stage uses its own frozen
        guidance selection.
      </p>
      <p>
        RD must declare exactly one signal day. The OE window must contain that
        day and later execution. The server checks the exact operator binding
        and combined budget on every preflight.
      </p>
      {models.error && <ErrorState error={models.error} />}
      <OEProviderSelection
        modelOnly
        snapshots={[]}
        snapshotId=""
        onSnapshot={() => {}}
        models={models.data ?? []}
        modelProfileId={request.model_profile_id}
        onModel={(id) =>
          onChange({
            ...request,
            model_profile_id: id,
            model_binding_sha256:
              models.data?.find((value) => value.binding.profile_id === id)
                ?.binding_sha256 ?? "",
          })
        }
      />
      {model && model.binding_sha256 !== request.model_binding_sha256 && (
        <p role="alert">
          Operator binding changed. Select the intended profile again and
          recheck; no automatic replacement.
        </p>
      )}
      <OEProviderParameters request={request} onChange={onChange} />
    </section>
  );
}
