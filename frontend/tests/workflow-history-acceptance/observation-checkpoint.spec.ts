import { test, expect } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { api, authorization, signIn, token } from "../e2e/helpers";
import type { WorkflowView } from "../../src/workflow-contracts/types.gen";

test("actual released observation is selected and resumed only at a safe checkpoint", async ({
  page,
  request,
}) => {
  const expected = JSON.parse(process.env.E2E_WORKFLOW_CHECKPOINT ?? "null");
  const output = process.env.E2E_WORKFLOW_CHECKPOINT_OUTPUT;
  if (
    !expected?.workflow_id ||
    !expected.run_id ||
    !expected.analysis_id ||
    !output
  )
    throw new Error(
      "Actual completed child, safe checkpoint and private receipt required",
    );
  const headers = authorization(await token(request));
  const workflowPath = `/research-workflows/${expected.workflow_id}`;
  const read = async (path: string) => {
    const response = await request.get(api(path), { headers });
    expect(response.status()).toBe(200);
    return response.json();
  };
  const before: WorkflowView = await read(workflowPath);
  expect(before.status).toBe("paused");
  expect(before.controls.resume).toBe(true);
  expect(before.current_stage?.stage_id).toBeTruthy();
  const stagePath = `${workflowPath}/stages/${before.current_stage!.stage_id}`;
  const stageBefore = await read(stagePath);
  const analysisPath = `/engine-runs/${expected.run_id}/analyses/${expected.analysis_id}`;
  await signIn(page);
  await page.goto(analysisPath);
  // A deliberate UI release, not an inferred grant from workflow opt-in.
  await page
    .getByRole("button", { name: "Release historical result", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Released direct analysis",
      exact: true,
    }),
  ).toBeVisible();
  const analysis = await read(analysisPath);
  const day = analysis.result.days[0];
  expect(day?.session).toBeTruthy();
  await page
    .getByLabel(`Use session ${day.session} as guidance context`, {
      exact: true,
    })
    .check();
  await page
    .getByRole("button", {
      name: "Prepare guidance from selected sessions",
      exact: true,
    })
    .click();
  const editor = page.getByRole("region", {
    name: "Review observation-bound guidance",
  });
  await expect(editor).toContainText(analysis.result_sha256);
  await editor
    .getByLabel("Your research direction", { exact: true })
    .fill(
      "Compare this observed session in the next squared-price experiment; retain the previous stage unchanged.",
    );
  await editor
    .getByLabel("I reviewed these exact references and my guidance.", {
      exact: true,
    })
    .check();
  const savedResponse = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname.endsWith("/rd-steers") &&
      r.request().method() === "POST",
  );
  await editor
    .getByRole("button", {
      name: "Save observation-bound steer version",
      exact: true,
    })
    .click();
  const response = await savedResponse;
  expect(response.status()).toBe(201);
  const saved = await response.json();
  expect(saved.current.text).toBe(response.request().postDataJSON().text);
  expect(saved.current.text_sha256).toBe(
    createHash("sha256").update(saved.current.text).digest("hex"),
  );
  for (const identity of [
    analysis.run_id,
    analysis.analysis_id,
    analysis.execution_snapshot_id,
    analysis.result_sha256,
    analysis.measurement_sha256,
    analysis.result.protocol_sha256,
  ])
    expect(saved.current.text).toContain(identity);
  expect(saved.current.text).toContain(day.session);
  expect(await read(workflowPath)).toEqual(before); // CRUD is not selection or resume.
  await page.goto(workflowPath);
  const selection = page.getByRole("region", {
    name: "Edit future workflow selection",
  });
  await selection
    .getByRole("button", {
      name: "Refresh available manual versions",
      exact: true,
    })
    .click();
  const option = selection
    .locator("div")
    .filter({ has: page.locator("p").filter({ hasText: saved.current.text }) });
  await option
    .getByRole("button", {
      name: "Select this exact version for a future stage",
      exact: true,
    })
    .click();
  const selectedResponse = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname.endsWith("/selected-inputs") &&
      r.request().method() === "POST",
  );
  await selection
    .getByRole("button", {
      name: "Save this exact ordered selection for a future stage",
      exact: true,
    })
    .click();
  const selected = await selectedResponse;
  expect(selected.status()).toBe(200);
  const body = selected.request().postDataJSON();
  const available = await read(
    "/research-workflow-inputs/manual?offset=0&limit=100",
  );
  const exactRef = available.items.find(
    (item: { ref: { version_id: string } }) =>
      item.ref.version_id === saved.current.version_id,
  ).ref;
  expect(body.expected_revision).toBe(before.revision);
  expect(body.refs).toContainEqual(exactRef);
  expect(await read(stagePath)).toEqual(stageBefore);
  const after = await read(workflowPath);
  expect(after.status).toBe("paused");
  expect(after.revision).toBeGreaterThan(before.revision);
  const stale = await request.post(api(`${workflowPath}/selected-inputs`), {
    headers,
    data: { ...body, idempotency_key: randomUUID() },
  });
  expect(stale.status()).toBe(409);
  expect(await read(workflowPath)).toEqual(after);
  await expect(
    page.getByText(
      `Current recorded state: paused; revision ${after.revision}.`,
      { exact: true },
    ),
  ).toBeVisible();
  // Explicit resume, still only the next dispatcher stage may consume selection.
  const resumedResponse = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname.endsWith("/commands") &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("region", { name: "Edit future workflow selection" })
    .getByRole("button", { name: "resume", exact: true })
    .click();
  const resumed = await resumedResponse;
  expect(resumed.status()).toBe(200);
  expect(resumed.request().postDataJSON().action).toBe("resume");
  expect(resumed.request().postDataJSON().expected_revision).toBe(
    after.revision,
  );
  expect(await read(stagePath)).toEqual(stageBefore);
  writeFileSync(
    output,
    JSON.stringify({
      saved,
      previous_stage: {
        stage_id: stageBefore.stage_id,
        input_revision_id: stageBefore.input_revision_id,
        definition_sha256: stageBefore.definition_sha256,
      },
      analysis_id: analysis.analysis_id,
      result_sha256: analysis.result_sha256,
      session: day.session,
      selection: body,
      resume: await resumed.json(),
    }),
    { mode: 0o600, flag: "wx" },
  );
});
