import { expect, it } from "vitest";
import type { NormalExecutionRecord } from "../../src/client/types.gen";
import { normalOEExecutionSelection as select } from "../../src/lib/normal-oe-execution-selection";
import { OENormalResults } from "../../src/features/oe-provider-results";
const id = "00000000-0000-0000-0000-000000000001";
const other = "00000000-0000-0000-0000-000000000002";
const record = {
  execution_id: id,
  candidate_id: other,
} as NormalExecutionRecord;
it("selects only one actual execution and candidate; no first/newest fallback", () => {
  expect(select(`?execution_id=${id}`, [record])).toEqual({
    kind: "execution",
    candidateId: other,
  });
  for (const records of [
    [],
    [record, record],
    [record, { ...record, execution_id: other }],
  ])
    expect(select(`?execution_id=${id}`, records)).toEqual({
      kind: "unavailable",
    });
  expect(select(`?execution_id=${other}`, [record])).toEqual({
    kind: "unavailable",
  });
});
it("rejects malformed/duplicate/extra query keys and delegates note protocol exclusively", () => {
  for (const search of [
    "?execution_id=bad",
    `?execution_id=${id}&execution_id=${id}`,
    `?execution_id=${id}&release=true`,
    "?candidate_id=bad",
  ])
    expect(select(search, [record])).toEqual({ kind: "unavailable" });
  expect(select("", [record])).toEqual({ kind: "none" });
  expect(select(`?normal_note_version=1&execution_id=${id}`, [record])).toEqual(
    { kind: "note" },
  );
});
it("keys the actual stateful results child by owner and run to force remount, not reuse old selection", () => {
  const props = { ownerId: id, runId: other, status: "succeeded" };
  const original = OENormalResults(props);
  expect(original.key).toBe(OENormalResults(props).key);
  expect(original.key).not.toBe(
    OENormalResults({ ...props, ownerId: other }).key,
  );
  expect(original.key).not.toBe(OENormalResults({ ...props, runId: id }).key);
});
