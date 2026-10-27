/** Called only by the genuine completed-run browser harness; no route mocking. */
import { expect, type Page } from "@playwright/test";

export async function assertFactorLibraryNavigation(
  page: Page,
  ids: {
    factorId: string;
    versionId: string;
    runId: string;
    snapshotId: string;
    analysisId: string;
  },
) {
  const prefix = process.env.E2E_APP_SUBPATH || "";
  const library = `${prefix}/factors/${ids.factorId}/versions/${ids.versionId}`;
  await page.goto(library);
  await expect(
    page.getByRole("heading", {
      name: "Research execution snapshots",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText(
      "Direct analysis result recorded; measurement is not released here.",
      { exact: true },
    ),
  ).toBeVisible();
  await page
    .getByRole("link", {
      name: `Open execution snapshot ${ids.snapshotId}`,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(
    new RegExp(
      `/engine-runs/${ids.runId}/snapshots/${ids.snapshotId}(?:\\?.*)?$`,
    ),
  );
  await page.reload();
  await expect(
    page.getByRole("button", { name: /release/i }).first(),
  ).toBeVisible();
  await page.goBack();
  await expect(
    page.getByRole("heading", {
      name: "Research execution snapshots",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("link", { name: "Open declared direct analysis", exact: true })
    .click();
  await expect(page).toHaveURL(
    new RegExp(
      `/engine-runs/${ids.runId}/analyses/${ids.analysisId}(?:\\?.*)?$`,
    ),
  );
  await page.goBack();
  await expect(
    page.getByRole("heading", {
      name: "Research execution snapshots",
      exact: true,
    }),
  ).toBeVisible();
  // No release button was clicked and no legacy snapshot route was used.
}
