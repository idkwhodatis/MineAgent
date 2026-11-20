import assert from "node:assert/strict";
import { test } from "vitest";
import { observeTokenPhase, tokenDiagnostic } from "../e2e/token-diagnostics";

test("successful operations run exactly once and return the same value", async () => {
  const result = {};
  let count = 0;
  assert.equal(
    await observeTokenPhase("request", null, async () => {
      count += 1;
      return result;
    }),
    result,
  );
  assert.equal(count, 1);
});

for (const [phase, status] of [
  ["request", null],
  ["http", 401],
  ["http", 429],
  ["http", 503],
  ["decode", 200],
] as const) {
  test(`same original error propagates with finite ${phase}/${status} annotation`, async () => {
    const error = new TypeError("PRIVATE credentials or response");
    const stack = error.stack;
    let count = 0;
    await assert.rejects(
      observeTokenPhase(phase, status, async () => {
        count += 1;
        throw error;
      }),
      (actual: unknown) => actual === error,
    );
    assert.equal(count, 1);
    assert.equal(
      error.message,
      "PRIVATE credentials or response\n" + tokenDiagnostic(phase, status),
    );
    assert.equal(error.stack, tokenDiagnostic(phase, status) + "\n" + stack);
    assert.equal(tokenDiagnostic(phase, status).includes("PRIVATE"), false);
  });
}

test("frozen and primitive thrown values remain original failures", async () => {
  for (const error of [Object.freeze(new Error("PRIVATE")), "PRIVATE", null]) {
    let caught;
    try {
      await observeTokenPhase("request", null, async () => {
        throw error;
      });
    } catch (value) {
      caught = value;
    }
    assert.equal(caught, error);
  }
});

test("invalid status is never serialized as arbitrary content", () => {
  for (const status of [NaN, Infinity, 99, 600, 401.5, "PRIVATE", true, {}]) {
    assert.equal(
      // @ts-expect-error Intentionally exercise an untrusted runtime value.
      tokenDiagnostic("request", status),
      'TOKEN_DIAGNOSTIC_V1:{"phase":"request","http_status":null}',
    );
  }
});
