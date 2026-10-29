import { test, expect, type APIRequestContext } from "@playwright/test";
import { z } from "zod";
import { createHash } from "node:crypto";
import type { InputDerivationView } from "../../src/input-audit-contracts/types.gen";
import { api, authorization, signIn, token } from "../e2e/helpers";
import type {
  ResearchInputs,
  ModelStageInputSnapshot,
} from "../../src/client/types.gen";
import type {
  WorkflowView,
  WorkflowStageList,
  WorkflowStageHistory,
} from "../../src/workflow-contracts/types.gen";

const uuid = z.uuid();
const hash = z.string().regex(/^[a-f0-9]{64}$/);
const observations = z.strictObject({
  workflow_id: uuid,
  status: z.string().min(1),
  blocked_reason: z.string().nullable(),
  mode: z.enum(["continuous", "pause_after_stage"]),
  plan_sha256: hash,
  allow_feedback_release: z.boolean(),
  stages: z
    .array(
      z.strictObject({
        stage_id: uuid,
        ordinal: z.number().int().positive(),
        kind: z.string().min(1),
        child_run_id: uuid.nullable(),
        input_revision_id: uuid,
        definition_sha256: hash,
        checkpoint_id: uuid.nullable(),
      }),
    )
    .min(1)
    .max(32),
  children: z
    .array(
      z.strictObject({
        run_id: uuid,
        config_sha256: hash,
        workflow_inputs_sha256: hash,
        workflow_inputs: z.record(z.string(), z.unknown()),
        source_id: uuid,
        factor_id: uuid,
        factor_version_id: uuid,
        snapshot_id: uuid,
        analysis_id: uuid,
      }),
    )
    .min(1)
    .max(2),
});

// This case reads the actual fixture while its real PostgreSQL/retained data
// remain alive. It neither mocks API responses nor launches/releases more work.
test("actual workflow history preserves frozen stages and provenance without new authority", async ({
  page,
  request,
}) => {
  const expected = observations.parse(
    JSON.parse(process.env.E2E_WORKFLOW_OBSERVATIONS ?? "null"),
  );
  const headers = authorization(await token(request));
  async function read<T>(
    path: string,
    client: APIRequestContext = request,
  ): Promise<T> {
    const response = await client.get(api(path), { headers });
    expect(response.status(), path).toBe(200);
    return response.json();
  }
  const path = `/research-workflows/${expected.workflow_id}`;
  const before = await read<WorkflowView>(path);
  expect(before.status).toBe(expected.status);
  expect(before.mode).toBe(expected.mode);
  expect(expected.children).toHaveLength(
    expected.allow_feedback_release ? 2 : 1,
  );
  if (!expected.allow_feedback_release)
    expect(expected.blocked_reason).toBe("workflow_feedback_release_required");
  expect(before.blocked_reason ?? null).toBe(expected.blocked_reason);
  const listing = await read<WorkflowStageList>(
    `${path}/stages?after_ordinal=0&limit=32`,
  );
  expect(listing.has_more).toBe(false);
  expect(
    listing.items.map(
      ({ stage_id, ordinal, kind, input_revision_id, definition_sha256 }) => ({
        stage_id,
        ordinal,
        kind,
        input_revision_id,
        definition_sha256,
      }),
    ),
  ).toEqual(
    expected.stages.map(
      ({ child_run_id: _, checkpoint_id: __, ...rest }) => rest,
    ),
  );
  await signIn(page);
  const mutations: string[] = [];
  page.on("request", (req) => {
    if (
      req.url().includes("/api/v1/") &&
      !["GET", "HEAD", "OPTIONS"].includes(req.method())
    )
      mutations.push(`${req.method()} ${new URL(req.url()).pathname}`);
  });
  await page.goto(path);
  await expect(
    page.getByRole("list", { name: "Recorded stage timeline" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: `Workflow ${expected.workflow_id}`,
      exact: true,
    }),
  ).toBeVisible();
  for (const stage of expected.stages) {
    const actual = await read<WorkflowStageHistory>(
      `${path}/stages/${stage.stage_id}`,
    );
    expect(actual.input_revision_id).toBe(stage.input_revision_id);
    expect(actual.child_run_id ?? null).toBe(stage.child_run_id);
    expect(actual.checkpoint_id ?? null).toBe(stage.checkpoint_id);
    await page
      .getByRole("navigation", { name: "Recorded workflow stages" })
      .getByRole("button", { name: new RegExp(`^Stage ${stage.ordinal}:`) })
      .click();
    await expect(
      page.getByRole("heading", {
        name: `Actual stage ${stage.ordinal}: ${stage.kind}`,
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByText(
        `Frozen stage input revision: ${stage.input_revision_id};`,
        { exact: false },
      ),
    ).toBeVisible();
    await page
      .getByRole("button", {
        name: "Refresh actual workflow history",
        exact: true,
      })
      .click();
    await expect(
      page.getByRole("heading", {
        name: `Actual stage ${stage.ordinal}: ${stage.kind}`,
        exact: true,
      }),
    ).toBeVisible();
    expect(new URL(page.url()).hash).toContain(
      `research_stage=${stage.stage_id}`,
    );
    await page.reload();
    await expect(
      page.getByRole("heading", {
        name: `Actual stage ${stage.ordinal}: ${stage.kind}`,
        exact: true,
      }),
    ).toBeVisible();
    if (
      (stage.kind === "external_normalization" ||
        stage.kind === "experiment_feedback_projection") &&
      actual.source_status !== "not_recorded"
    ) {
      const receipt = await read<InputDerivationView>(
        `${path}/stages/${stage.stage_id}/input-derivation`,
      );
      expect(receipt.workflow_id).toBe(expected.workflow_id);
      expect(receipt.stage_id).toBe(stage.stage_id);
      expect(receipt.operation).toBe(stage.kind);
      const audit = page.getByRole("region", {
        name: "Input derivation audit",
        exact: true,
      });
      await expect(audit).toBeVisible();
      await expect(audit).toContainText(receipt.derivation_id);
      if (receipt.raw_object_id && receipt.raw_input_sha256) {
        const rawPath = `/api/v1/research-inputs/external-objects/${receipt.raw_object_id}/content`;
        const response = page.waitForResponse(
          (response) =>
            new URL(response.url()).pathname === rawPath &&
            response.request().method() === "GET",
        );
        await audit
          .getByRole("button", { name: "View input", exact: true })
          .click();
        const retained = await response;
        expect(retained.status()).toBe(200);
        expect(
          new URL(retained.url()).searchParams.get("expected_sha256"),
        ).toBe(receipt.raw_input_sha256);
        expect(
          createHash("sha256")
            .update(await retained.body())
            .digest("hex"),
        ).toBe(receipt.raw_input_sha256);
        await expect(
          audit.getByRole("region", {
            name: "Explicitly requested retained content",
          }),
        ).toBeVisible();
        await expect(audit).toContainText("Viewing is not approval");
      }
    }
    if (stage.child_run_id) {
      const child = expected.children.find(
        (child) => child.run_id === stage.child_run_id,
      );
      expect(child).toBeDefined();
      expect(actual.output_refs).toEqual([
        expect.objectContaining({
          output_kind: "direct_analysis",
          output_id: child!.analysis_id,
          child_run_id: child!.run_id,
        }),
      ]);
      await expect(
        page.getByRole("link", {
          name: `Open exact direct_analysis ${child!.analysis_id}`,
          exact: true,
        }),
      ).toHaveAttribute(
        "href",
        `/engine-runs/${child!.run_id}/analyses/${child!.analysis_id}`,
      );
      expect(actual.containment).toBe("positive_stop");
      expect(actual.settlement).toBe("settled");
      expect(
        actual.model_requests?.some(
          (item) => item.state === "included_in_submitted_prompt",
        ),
      ).toBe(true);
    }
    if (actual.automatic_feedback_refs?.length) {
      const section = page.getByRole("region", {
        name: "Automatically included experiment feedback",
      });
      await expect(section).toBeVisible();
      await section
        .getByRole("button", { name: "View feedback output", exact: true })
        .click();
      await expect(
        section.getByRole("region", {
          name: "Explicitly requested retained content",
        }),
      ).toBeVisible();
    }
  }
  await page.reload();
  await expect(
    page.getByRole("heading", {
      name: `Workflow ${expected.workflow_id}`,
      exact: true,
    }),
  ).toBeVisible();
  for (const child of expected.children) {
    const input = await read<ResearchInputs>(
      `/engine-runs/${child.run_id}/inputs`,
    );
    const receipts = input.items.filter(
      (item) => item.origin === "workflow_stage",
    );
    expect(receipts).toHaveLength(1);
    expect(receipts[0].default_pinned).toBe(false);
    expect(receipts[0].workflow_inputs_sha256).toBe(
      child.workflow_inputs_sha256,
    );
    expect(receipts[0].workflow_inputs).toEqual(child.workflow_inputs);
    await page.goto(`/engine-runs/${child.run_id}`);
    const frozen = page.getByRole("region", {
      name: "Frozen workflow inputs by origin",
    });
    await expect(frozen).toBeVisible();
    const snapshot = child.workflow_inputs as ModelStageInputSnapshot;
    const manual = (snapshot.inputs ?? []).filter(
      (item) => item.ref.origin === "manual_user_submission",
    );
    await expect(
      frozen.getByLabel("Pinned manual guidance", { exact: true }),
    ).toHaveCount(manual.length);
    for (const [index, item] of (snapshot.inputs ?? []).entries()) {
      await expect(
        frozen.getByText(
          `Frozen position ${index + 1} · ${item.ref.version_id}`,
          { exact: true },
        ),
      ).toBeVisible();
      await expect(frozen.getByText(item.text, { exact: true })).toBeVisible();
    }
    await page.reload();
    await expect(frozen).toBeVisible();
    await page.goto(path);
    await page.goBack();
    await expect(frozen).toBeVisible();
  }
  const other = authorization(await token(request, true));
  for (const denied of [
    path,
    `${path}/stages?after_ordinal=0&limit=32`,
    ...expected.children.map((child) => `/engine-runs/${child.run_id}/inputs`),
  ]) {
    const response = await request.get(api(denied), { headers: other });
    expect(response.status()).toBe(404);
  }
  expect(await read<WorkflowView>(path)).toEqual(before);
  expect(mutations).toEqual([]);

  // Explicitly save a user-reviewed observation version through the REAL API.
  // Saving does not select it, mutate historical output, resume or launch work.
  await page.goto(path);
  const retainedStage = expected.stages[0];
  await page
    .getByRole("navigation", { name: "Recorded workflow stages" })
    .getByRole("button", {
      name: new RegExp(`^Stage ${retainedStage.ordinal}:`),
    })
    .click();
  await page
    .getByRole("button", {
      name: "Prepare guidance from this observation",
      exact: true,
    })
    .click();
  const editor = page.getByRole("region", {
    name: "Review observation-bound guidance",
  });
  await expect(editor).toContainText(retainedStage.stage_id);
  await expect(editor).toContainText(retainedStage.definition_sha256);
  await editor
    .getByLabel("Your research direction", { exact: true })
    .fill(
      "Compare this retained stage in the next eligible experiment; do not rewrite it.",
    );
  await editor
    .getByLabel("I reviewed these exact references and my guidance.", {
      exact: true,
    })
    .check();
  const posted = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname.endsWith("/api/v1/rd-steers") &&
      response.request().method() === "POST",
  );
  await editor
    .getByRole("button", {
      name: "Save observation-bound steer version",
      exact: true,
    })
    .click();
  const response = await posted;
  expect(response.status()).toBe(201);
  const saved = await response.json();
  expect(response.request().postDataJSON().text).toBe(saved.current.text);
  expect(saved.current.text).toContain(retainedStage.definition_sha256);
  await expect(editor).toContainText(saved.current.version_id);
  await expect(editor).toContainText("Saved, not selected or consumed");
  const history = await read<{
    data: Array<{ version_id: string; text: string }>;
  }>(`/rd-steers/${saved.id}/versions`);
  expect(
    history.data.some(
      (item) =>
        item.version_id === saved.current.version_id &&
        item.text === saved.current.text,
    ),
  ).toBe(true);
  expect(await read<WorkflowView>(path)).toEqual(before);
  expect(
    (
      await read<WorkflowStageHistory>(
        `${path}/stages/${retainedStage.stage_id}`,
      )
    ).definition_sha256,
  ).toBe(retainedStage.definition_sha256);
  expect(mutations.filter((value) => !value.endsWith("/rd-steers"))).toEqual(
    [],
  );
  expect(
    mutations.filter((value) => value.endsWith("/rd-steers")),
  ).toHaveLength(1);
});
