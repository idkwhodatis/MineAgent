import { test, expect, type Page } from "@playwright/test";
import { token, create, api, authorization } from "../e2e/helpers";

const prefix = process.env.E2E_SUBPATH;
const origin = process.env.E2E_SUBPATH_ORIGIN;
if (prefix === undefined || !origin || !process.env.E2E_SUBPATH_REPORT)
  throw new Error("Explicit real Caddy acceptance environment required");
const href = (path: string) => `${origin}${prefix}${path}`;
async function login(
  page: Page,
  expected = "Research overview",
  navigate = true,
) {
  if (navigate) await page.goto(href("/login"));
  await page.getByLabel("Email address").fill(process.env.E2E_EMAIL!);
  await page
    .getByLabel("Password", { exact: true })
    .fill(process.env.E2E_PASSWORD!);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: expected, exact: true }),
  ).toBeVisible();
}

test("real login, deep-link reload, assets and logout stay under runtime mount", async ({
  page,
  request,
}) => {
  // The internal frontend is also Caddy: SPA fallback must never swallow API
  // errors, and both root and direct index requests use the runtime-rendered HTML.
  const missingApi = await request.get(href("/api/v1/not-a-real-route"));
  expect(missingApi.status()).toBe(404);
  expect(missingApi.headers()["cache-control"]).toBe("no-store");
  expect(missingApi.headers()["x-content-type-options"]).toBe("nosniff");
  expect(missingApi.headers()["content-type"]).toContain("application/json");
  expect(await missingApi.text()).not.toContain("data-mineagent-base");
  const index = await request.get(href("/index.html"));
  expect(index.status()).toBe(200);
  expect(index.headers()["x-content-type-options"]).toBe("nosniff");
  expect(index.headers()["cache-control"]).toBe("no-store");
  expect(await index.text()).toContain(`data-mineagent-base href="${prefix}/"`);
  const escaped: string[] = [];
  const failedAssets: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (
      url.origin === origin &&
      prefix &&
      !url.pathname.startsWith(`${prefix}/`)
    )
      escaped.push(url.pathname);
  });
  page.on("response", (response) => {
    if (
      ["script", "stylesheet"].includes(response.request().resourceType()) &&
      !response.ok()
    )
      failedAssets.push(new URL(response.url()).pathname);
  });
  await page.goto(href("/runs"));
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL((url) => url.pathname === `${prefix}/login`);
  await login(page, "Experiments", false);
  await expect(page).toHaveURL((url) => url.pathname === `${prefix}/runs`);
  await page.goto(href("/runs"));
  await expect(
    page.getByRole("heading", { name: "Experiments", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Experiments", exact: true }),
  ).toBeVisible();
  await expect(page.locator("script[src]")).not.toHaveCount(0);
  // New engine history must use the same real Caddy mount, not root URLs or
  // intercepted responses. The full saved-source browser gate is separate.
  await page.getByRole("link", { name: "Engine history", exact: true }).click();
  await expect(page).toHaveURL(
    (url) => url.pathname === `${prefix}/engine-runs`,
  );
  await expect(
    page.getByRole("heading", { name: "Engine research history", exact: true }),
  ).toBeVisible();
  const auth = await token(request);
  const capability = await request.get(api("/engine-runs/capabilities"), {
    headers: authorization(auth),
  });
  expect(capability.status()).toBe(200);
  expect(
    (await capability.json()).engines.every(
      (item: { can_create: boolean }) => item.can_create === false,
    ),
  ).toBe(true);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Engine research history", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("link", { name: "Check RD-Agent dataset eligibility" })
    .click();
  await expect(page).toHaveURL(
    (url) => url.pathname === `${prefix}/engine-runs/preflight`,
  );
  await expect(
    page.getByRole("heading", {
      name: "Check RD-Agent research eligibility",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Create run — not enabled" }),
  ).toBeDisabled();
  await page.reload();
  await expect(
    page.getByLabel("Retained dataset", { exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Back to engine history" }).click();
  const unknown = "00000000-0000-0000-0000-000000000000";
  const failedDetail = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        `${prefix}/api/v1/engine-runs/${unknown}` && response.status() === 404,
  );
  await page.goto(href(`/engine-runs/${unknown}`));
  await failedDetail;
  await expect(page.getByText(/not found/i).first()).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Seed evaluation", exact: true }),
  ).toHaveCount(0);
  await page.reload();
  await expect(page.getByText(/not found/i).first()).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(
    (url) => url.pathname === `${prefix}/engine-runs`,
  );
  await expect(
    page.getByRole("heading", { name: "Engine research history", exact: true }),
  ).toBeVisible();
  // Actual Caddy deep-link/reload/error coverage, not a saved-result claim.
  const structured = await request.get(
    api("/structured-executions/capabilities"),
    { headers: authorization(auth) },
  );
  expect(structured.status()).toBe(200);
  const structuredCapability = await structured.json();
  expect(structuredCapability.can_create).toBe(false);
  for (const path of [
    `/engine-runs/${unknown}/structured-executions/${unknown}`,
    `/engine-runs/${unknown}/structured-comparison?before=${unknown}&after=11111111-1111-1111-1111-111111111111`,
  ]) {
    await page.goto(href(path));
    if (structuredCapability.can_read)
      await expect(page.getByText(/not found/i).first()).toBeVisible();
    else
      await expect(
        page.getByRole("heading", {
          name: "Structured history unavailable",
          exact: true,
        }),
      ).toBeVisible();
    await expect(
      page.getByRole("heading", {
        name: "Observed closed-program comparison",
        exact: true,
      }),
    ).toHaveCount(0);
    await page.reload();
    if (structuredCapability.can_read)
      await expect(page.getByText(/not found/i).first()).toBeVisible();
    else
      await expect(
        page.getByRole("heading", {
          name: "Structured history unavailable",
          exact: true,
        }),
      ).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(
      (url) => url.pathname === `${prefix}/engine-runs`,
    );
  }
  // New timeline deep links carry opaque stage identity in the fragment only.
  // A missing workflow must never manufacture a timeline or a steering grant.
  const workflowCapabilityResponse = await request.get(
    api("/research-workflows/capabilities"),
    { headers: authorization(auth) },
  );
  expect([200, 404]).toContain(workflowCapabilityResponse.status());
  const workflowReadable =
    workflowCapabilityResponse.status() === 200 &&
    (await workflowCapabilityResponse.json()).can_read === true;
  const fragment = `#research_workflow=${unknown}&research_stage=${unknown}`;
  await page.goto(href(`/research-workflows/${unknown}${fragment}`));
  for (let attempt = 0; attempt < 2; attempt += 1) {
    if (attempt) await page.reload();
    await expect(
      page.getByRole("link", { name: "Back to workflow entry", exact: true }),
    ).toHaveAttribute("href", `${prefix}/research-workflows`);
    if (workflowReadable)
      await expect(page.getByText(/not found/i).first()).toBeVisible();
    else
      await expect(
        page.getByText(
          "Workflow history is not enabled by the installed server capability.",
          { exact: true },
        ),
      ).toBeVisible();
    expect(new URL(page.url()).hash).toBe(fragment);
    await expect(
      page.getByRole("list", { name: "Recorded stage timeline" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", {
        name: "Save observation-bound steer version",
        exact: true,
      }),
    ).toHaveCount(0);
  }
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
  await page.goto(href("/runs"));
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
  expect(escaped).toEqual([]);
  expect(failedAssets).toEqual([]);
  if (prefix) {
    const redirect = await request.get(`${origin}${prefix}`, {
      maxRedirects: 0,
    });
    expect(redirect.status()).toBe(308);
    expect(redirect.headers().location).toBe(`${prefix}/`);
    expect((await request.get(`${origin}/api/v1/health/ready`)).status()).toBe(
      404,
    );
  }
});

test("real factor export download and authenticated run SSE cross Caddy", async ({
  page,
  request,
}) => {
  const auth = await token(request);
  const headers = authorization(auth);
  const redirect = await request.get(api("/runs/"), {
    headers,
    maxRedirects: 0,
  });
  expect(redirect.status()).toBe(307);
  expect(new URL(redirect.headers().location, origin).href).toBe(
    href("/api/v1/runs"),
  );
  const escaped: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (
      url.origin === origin &&
      prefix &&
      !url.pathname.startsWith(`${prefix}/`)
    )
      escaped.push(url.pathname);
  });
  const response = await request.post(api("/factor-fixtures"), {
    headers: { ...headers, "Idempotency-Key": crypto.randomUUID() },
    data: {
      kind: "momentum",
      name: `Subpath ${crypto.randomUUID()}`,
      lookback_sessions: 2,
      lag_sessions: 0,
    },
  });
  expect(response.status()).toBe(201);
  const fixture = await response.json();
  await login(page);
  const deep = href(
    `/factors/${fixture.factor.id}/versions/${fixture.version.id}?snapshot=${fixture.snapshot.id}&tab=evidence`,
  );
  await page.goto(deep);
  await page.reload();
  await page.getByLabel("Include selected snapshot values").check();
  const downloadPromise = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Export exact factor package", exact: true })
    .click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe(`factor-${fixture.version.id}.zip`);
  expect(await download.failure()).toBeNull();
  const run = await create(request, auth);
  const events = await request.get(api(`/runs/${run.id}/events?follow=false`), {
    headers,
  });
  expect(events.status()).toBe(200);
  expect(events.headers()["content-type"]).toContain("text/event-stream");
  expect(await events.text()).toContain("data:");
  expect(
    (await request.get(api(`/runs/${run.id}/events?follow=false`))).status(),
  ).toBe(401);
  const stream = page.waitForResponse(
    (r) => r.url().includes(`/runs/${run.id}/events`) && r.status() === 200,
  );
  await page.goto(href(`/runs/${run.id}`));
  const observed = await stream;
  expect(escaped).toEqual([]);
  expect(new URL(observed.url()).pathname).toBe(
    `${prefix}/api/v1/runs/${run.id}/events`,
  );
});
