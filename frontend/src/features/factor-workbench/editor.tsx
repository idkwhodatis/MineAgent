import { useRef, useState } from "react";
import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { ErrorState, Panel } from "@/components/common";
import { Field } from "@/components/data-common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  factorRequest,
  type FactorPresentation,
  type FactorVersion,
} from "@/lib/factor-api";
import {
  factorExposureReads,
  requireFactorReadsIdle,
} from "@/lib/factor-publication";
import { forgetFactorSubmission, submissionKey } from "./selection";

const initialDefinition = {
  schema_version: "mineagent.factor-definition.v1",
  factor_kind: "time_series",
  frequency: "1d",
  input_fields: [
    {
      name: "close",
      unit: "price",
      dtype: "float64",
      price_semantics: "unadjusted",
      role: "market_input",
    },
  ],
  lookback_sessions: 20,
  lag_sessions: 0,
  calculation_universe_policy: "dataset_inventory",
  missing_policy: "explicit_missing",
  parameters: [],
  preprocessing: { kind: "none" },
  output_unit: "ratio",
  interpretation: "higher_preferred",
};
export async function exactSourceHash(code: string): Promise<string> {
  const bytes = new TextEncoder().encode(code);
  if (bytes.length > 131_072 || !code.trim() || code.includes("\0"))
    throw new Error(
      "Enter nonempty source, at most 128 KiB, without NUL characters.",
    );
  return Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
  )
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
export function FactorEditor({
  owner,
  parent,
  onSaved,
  onClose,
}: {
  owner: string;
  parent?: FactorVersion;
  onSaved: (factorId: string, versionId: string) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [code, setCode] = useState(parent?.code || "");
  const [definition, setDefinition] = useState(
    JSON.stringify(parent?.definition || initialDefinition, null, 2),
  );
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const active = useRef(false);
  const queries = useQueryClient();
  const readsPending = useIsFetching(factorExposureReads) > 0;
  async function save() {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    setError(undefined);
    try {
      requireFactorReadsIdle(queries);
      const parsed: unknown = JSON.parse(definition);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
        throw new Error("Definition must be a JSON object.");
      const body = {
        ...(!parent ? { name } : {}),
        code,
        definition: { ...parsed, code_sha256: await exactSourceHash(code) },
        parent_version_id: parent?.id || null,
        ...(note ? { change_note: note } : {}),
      };
      requireFactorReadsIdle(queries);
      const operation = parent ? `version.${parent.factor_id}` : "create";
      const result = await factorRequest<
        FactorVersion | { factor: FactorPresentation; version: FactorVersion }
      >(parent ? `/factors/${parent.factor_id}/versions` : "/factors", {
        method: "POST",
        body,
        key: submissionKey(owner, operation, body),
      });
      const version = "version" in result ? result.version : result;
      forgetFactorSubmission(owner, operation);
      await queries.invalidateQueries({ queryKey: ["factors"] });
      onSaved(version.factor_id, version.id);
    } catch (err) {
      setError(err);
    } finally {
      active.current = false;
      setBusy(false);
    }
  }
  return (
    <Panel
      title={
        parent
          ? `New child of v${parent.ordinal}`
          : "Save an independent factor"
      }
    >
      <p className="notice">
        Saving retains exact source and the definition only. It does not execute
        Python, materialize values, or certify financial performance. Review the
        example input contract before saving.
      </p>
      <form
        className="factor-editor"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        {!parent && (
          <Field id="factor-name" label="Factor name">
            <Input
              id="factor-name"
              required
              maxLength={240}
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={busy}
            />
          </Field>
        )}
        <Field
          id="factor-source"
          label="Exact Python source"
          help="Whitespace and line endings contribute to source identity. No formatter is applied."
        >
          <textarea
            id="factor-source"
            required
            rows={10}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            disabled={busy}
            spellCheck={false}
          />
        </Field>
        <Field
          id="factor-definition"
          label="Immutable input and output definition (JSON)"
          help="The source SHA-256 is computed from your exact source at submission. All other fields are validated by the server."
        >
          <textarea
            id="factor-definition"
            required
            rows={14}
            value={definition}
            onChange={(e) => setDefinition(e.target.value)}
            disabled={busy}
            spellCheck={false}
          />
        </Field>
        <Field id="factor-change" label="Human change note">
          <Input
            id="factor-change"
            maxLength={2000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            disabled={busy}
          />
        </Field>
        {error !== undefined && <ErrorState error={error} />}
        <div className="actions">
          <Button type="submit" disabled={busy || readsPending}>
            {busy ? "Saving…" : "Save exact definition"}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={onClose}
          >
            Close editor
          </Button>
        </div>
        {error !== undefined && (
          <p className="muted">
            A lost response may still have saved the definition. Retrying
            unchanged input uses the same submission key.
          </p>
        )}
      </form>
    </Panel>
  );
}
