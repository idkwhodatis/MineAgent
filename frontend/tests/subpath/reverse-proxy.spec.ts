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
