import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import {
  WorkflowStageNavigation,
  workflowStageTargets,
} from "../../src/features/workflow-stage-navigation";
import type { WorkflowStageList } from "../../src/workflow-contracts/types.gen";
import { readWorkflowStages } from "../../src/lib/research-workflows";
import { factorRequest } from "../../src/lib/factor-api";
vi.mock("../../src/lib/factor-api", () => ({ factorRequest: vi.fn() }));

const page: WorkflowStageList = {
  workflow_id: "30000000-0000-4000-8000-000000000001",
  after_ordinal: 0,
  has_more: true,
  qualification: "navigation_metadata_not_stage_completion_or_authority",
  items: [
    {
      stage_id: "30000000-0000-4000-8000-000000000002",
      ordinal: 1,
      kind: "rd_factor_generation",
      input_revision_id: "30000000-0000-4000-8000-000000000003",
      definition_sha256: "a".repeat(64),
      created_at: "2026-10-27T00:00:00Z",
    },
  ],
};
const noop = () => {};
function markup(value: WorkflowStageList, pending = false) {
  return renderToStaticMarkup(
    createElement(WorkflowStageNavigation, {
      page: value,
      pending,
      currentStageId: "current",
      selectedStageId: page.items[0].stage_id,
      canPrevious: false,
      onCurrent: noop,
      onSelect: noop,
      onPrevious: noop,
      onNext: noop,
    }),
  );
}
it("historical selection does not retarget current steering and stays selected as current advances", () => {
  expect(workflowStageTargets("current", "old")).toEqual({
    currentStageId: "current",
    stageId: "old",
  });
  expect(workflowStageTargets("new-current", "old")).toEqual({
    currentStageId: "new-current",
    stageId: "old",
  });
  expect(workflowStageTargets("new-current")).toEqual({
    currentStageId: "new-current",
    stageId: "new-current",
  });
});
it("labels metadata as navigation only and loads exact selected stage separately", () => {
  const html = markup(page);
  expect(html).toContain("Navigation metadata only");
  expect(html).toContain('aria-current="step"');
  expect(html).toContain("Stage 1");
  expect(html).not.toContain("completed");
  expect(html).not.toContain("approved");
});
it("empty or pending pages cannot advance", () => {
  expect(markup({ ...page, items: [], has_more: false })).toContain(
    'disabled="">More stages',
  );
  expect(markup(page, true)).toContain('disabled="">More stages');
});
it("requests bounded cursor page without changing a workflow", async () => {
  const signal = new AbortController().signal;
  await readWorkflowStages(page.workflow_id, 32, signal);
  expect(factorRequest).toHaveBeenCalledWith(
    `/research-workflows/${page.workflow_id}/stages?after_ordinal=32&limit=32`,
    { signal },
  );
});

it("renders exact input version and definition commitments in an accessible timeline", () => {
  const html = markup(page);
  expect(html).toContain('aria-label="Recorded stage timeline"');
  expect(html).toContain(page.items[0].input_revision_id);
  expect(html).toContain(page.items[0].definition_sha256);
  expect(html).toContain("ordering does not prove success or a dependency");
});
it("scopes deep links to the actual workflow and preserves unrelated fragment state", async () => {
  const { stageSelectionFromHash, stageSelectionHash } =
    await import("../../src/features/workflow-stage-navigation");
  const stage = page.items[0].stage_id;
  const hash = stageSelectionHash("#panel=history", page.workflow_id, stage);
  expect(stageSelectionFromHash(hash, page.workflow_id)).toBe(stage);
  expect(stageSelectionFromHash(hash, "different-workflow")).toBeUndefined();
  expect(
    stageSelectionFromHash(hash.replace(stage, "malformed"), page.workflow_id),
  ).toBeUndefined();
  expect(stageSelectionHash(hash, page.workflow_id)).toBe("#panel=history");
  expect(() => stageSelectionHash("", page.workflow_id, "bad")).toThrow();
});
