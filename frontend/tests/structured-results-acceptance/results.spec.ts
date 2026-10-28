import { test, expect } from "@playwright/test";
import { account, api, authorization, token } from "../e2e/helpers";
function id(name: string) {
  const value = process.env[name];
  if (!value || !/^[0-9a-f-]{36}$/i.test(value))
    throw Error(`Missing actual identity ${name}`);
  return value;
}
test("actual structured pair requires both releases and links factor decisions to observed fills", async ({
  page,
  request,
}) => {
  const run = id("E2E_STRUCTURED_RUN_ID"),
    before = id("E2E_BEFORE_EXECUTION_ID"),
    after = id("E2E_AFTER_EXECUTION_ID");
  const factor = id("E2E_FACTOR_ID"),
    version = id("E2E_FACTOR_VERSION_ID");
  const headers = authorization(await token(request));
  const pairPath = `/engine-runs/${run}/structured-comparison?before=${before}&after=${after}`;
  const executionPath = (value: string) =>
    `/engine-runs/${run}/structured-executions/${value}`;
  for (const path of [pairPath, executionPath(before), executionPath(after)]) {
    const response = await request.get(api(path), { headers });
    expect(response.status()).toBe(409);
    expect((await response.json()).code).toBe("engine_result_release_required");
  }
  const user = account();
  await page.goto("/login");
  await page.getByLabel("Email address").fill(user.email);
  await page.getByLabel("Password", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Research overview" }),
  ).toBeVisible();
  await page.goto("/factors");
  await expect(
    page.getByRole("heading", { name: "Factor library", exact: true }),
  ).toBeVisible();
  await page
    .locator(`a[href^="/factors/${factor}/versions/${version}"]`)
    .click();
  await page
    .getByRole("link", {
      name: `Open observed strategy execution ${before}`,
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Observed structured execution",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("link", { name: /Compare with exact factor version/ })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Release both exact executions before comparing",
    }),
  ).toBeVisible();
  await expect(
    page.getByText("Observed difference", { exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", {
      name: `Release before execution ${before}`,
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("button", {
      name: `Release after execution ${after}`,
      exact: true,
    }),
  ).toBeEnabled();
  expect((await request.get(api(pairPath), { headers })).status()).toBe(409);
  await expect(
    page.getByText("Observed difference", { exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", {
      name: `Release after execution ${after}`,
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("heading", { name: "Observed closed-program comparison" }),
  ).toBeVisible();
  const response = await request.get(api(pairPath), { headers });
  expect(response.status()).toBe(200);
  const pair = await response.json();
  expect(pair.before.execution_id).toBe(before);
  expect(pair.after.execution_id).toBe(after);
  expect(pair.before.factor_version_id).not.toBe(pair.after.factor_version_id);
  expect(pair.before.parent_factor_version_id).toBeNull();
  expect(pair.after.parent_factor_version_id).toBeNull();
  for (const side of [pair.before, pair.after]) {
    expect(
      side.decisions.map((item: { event_seq: number }) => item.event_seq),
    ).toEqual([0, 1, 2]);
    const [warmup, signal, hold] = side.decisions;
    expect(warmup.observation.warmup).toBe(true);
    expect(warmup.reply.decision.action).toBe("no_decision");
    expect(warmup.reads).toEqual([]);
    expect(signal.observation.warmup).toBe(false);
    expect(signal.reply.decision.action).toBe("rebalance");
    expect(signal.reads).toHaveLength(3);
    expect(hold.observation.warmup).toBe(false);
    expect(hold.reply.decision.action).toBe("hold");
    expect(hold.reads).toEqual([]);
    expect(side.orders.length).toBeGreaterThan(0);
    expect(side.fills.length).toBeGreaterThan(0);
    const warmupElement = page.locator(
      `[id="structured-${side.execution_id}-decision-${warmup.event_seq}"]`,
    );
    await expect(warmupElement).toContainText(warmup.observation.decision_time);
    await expect(warmupElement).toContainText("warmup");
    const decision = page.locator(
      `[id="structured-${side.execution_id}-decision-${signal.event_seq}"]`,
    );
    await expect(decision).toContainText(signal.observation.decision_time);
    for (const read of signal.reads)
      await expect(decision).toContainText(String(read.value));
    const fill = side.fills[0];
    const fillElement = page.locator(
      `[id="structured-${side.execution_id}-fill-${encodeURIComponent(fill.native_fill_id)}"]`,
    );
    await expect(fillElement).toContainText(
      `quantity ${fill.quantity}; price ${fill.price}`,
    );
    await fillElement
      .getByRole("link", {
        name: `Back to observed order ${fill.native_order_id}`,
      })
      .click();
    await expect(page).toHaveURL(
      new RegExp(`#structured-${side.execution_id}-order-`),
    );
    const order = side.orders.find(
      (item: { native_order_id: string }) =>
        item.native_order_id === fill.native_order_id,
    );
    expect(order).toBeDefined();
    await page
      .locator(
        `[id="structured-${side.execution_id}-order-${encodeURIComponent(fill.native_order_id)}"]`,
      )
      .getByRole("link", {
        name: `Back to decision ${order.decision_sequence}`,
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(
      new RegExp(
        `#structured-${side.execution_id}-decision-${order.decision_sequence}$`,
      ),
    );
  }
  const source = pair.before;
  await page
    .getByRole("link", {
      name: `Open exact execution snapshot ${source.execution_snapshot_id}`,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(
    new RegExp(
      `/engine-runs/${source.execution_snapshot_run_id}/snapshots/${source.execution_snapshot_id}`,
    ),
  );
  await expect(
    page.getByRole("heading", {
      name: "Explicit historical result release required",
    }),
  ).toBeVisible();
  await page.goBack();
  await expect(
    page.getByRole("heading", { name: "Observed closed-program comparison" }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Observed closed-program comparison" }),
  ).toBeVisible();
  await page
    .getByRole("link", { name: `Exact factor version ${version}`, exact: true })
    .click();
  await expect(page).toHaveURL(
    new RegExp(`/factors/${factor}/versions/${version}`),
  );
  await page.goBack();
  await expect(
    page.getByRole("heading", { name: "Observed closed-program comparison" }),
  ).toBeVisible();
  const otherHeaders = authorization(await token(request, true));
  for (const path of [
    pairPath,
    executionPath(before),
    executionPath(after),
    `/engine-runs/${run}/structured-execution-records`,
    `/factors/${factor}/versions/${version}/structured-executions`,
  ])
    expect(
      (await request.get(api(path), { headers: otherHeaders })).status(),
    ).toBe(404);
  for (const value of [before, after])
    expect(
      (
        await request.post(api(executionPath(value) + "/release"), {
          headers: otherHeaders,
        })
      ).status(),
    ).toBe(404);
});
