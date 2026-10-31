import { expect, it } from "vitest";
import { submitHttpDiagnostic } from "../oe-normal-acceptance/http-diagnostic";

it("projects only closed submit status and public code, never error data", () => {
  expect(
    submitHttpDiagnostic(503, {
      code: "database_unavailable",
      message: "private",
      correlation_id: "secret",
    }),
  ).toBe("OE_SUBMIT_HTTP_V1:503:database_unavailable");
  expect(submitHttpDiagnostic(409, { code: "preflight_changed" })).toBe(
    "OE_SUBMIT_HTTP_V1:409:preflight_changed",
  );
  for (const body of [
    null,
    "secret",
    { code: "private" },
    { code: "run_quota\nsecret" },
  ])
    expect(submitHttpDiagnostic("503-secret", body)).toBe(
      "OE_SUBMIT_HTTP_V1:other:other",
    );
  expect(submitHttpDiagnostic(599, {})).toBe("OE_SUBMIT_HTTP_V1:other:other");
});
