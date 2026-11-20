import { afterEach, expect, it, vi } from "vitest";
import {
  comparisonStatusDiagnostic,
  withComparisonFailureDiagnostic,
  comparisonDiagnosticPrefix,
} from "../e2e/comparison-status-diagnostics";
import type { Page } from "@playwright/test";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function page(statuses: string[], notices: string[] = []) {
  vi.stubGlobal("document", {
    querySelectorAll: () =>
      statuses.map((textContent) => ({
        textContent,
        parentElement: {
          querySelectorAll: () =>
            notices.map((textContent) => ({ textContent })),
        },
      })),
  });
  return { evaluate: vi.fn(async (fn, args) => fn(args)) } as unknown as Pick<
    Page,
    "evaluate"
  >;
}
it("classifies one status and known notice without arbitrary text", async () => {
  expect(
    await comparisonStatusDiagnostic(
      page(
        ["failed"],
        [
          "Comparison failed or interrupted: comparison_timeout. No failed run is represented as a zero result.",
        ],
      ),
    ),
  ).toEqual({
    presence: "single",
    status: "failed",
    error_code: "comparison_timeout",
  });
  const value = await comparisonStatusDiagnostic(page(["secret"], ["secret"]));
  expect(value).toEqual({
    presence: "single",
    status: "unrecognized",
    error_code: "unrecognized",
  });
  expect(JSON.stringify(value)).not.toContain("secret");
});
it("distinguishes missing and multiple status elements", async () => {
  for (const [values, presence] of [
    [[], "missing"],
    [["failed", "secret"], "multiple"],
  ] as const) {
    expect(await comparisonStatusDiagnostic(page([...values]))).toEqual({
      presence,
      status: "unrecognized",
      error_code: null,
    });
  }
});
it("bounds text and never extracts embedded or arbitrary codes", async () => {
  for (const raw of [
    "x".repeat(257),
    "prefix Comparison failed or interrupted: comparison_timeout. No failed run is represented as a zero result.",
    "Comparison failed or interrupted: secret. No failed run is represented as a zero result.",
  ]) {
    expect(
      await comparisonStatusDiagnostic(page(["x".repeat(65)], [raw])),
    ).toEqual({
      presence: "single",
      status: "unrecognized",
      error_code: "unrecognized",
    });
  }
});
it("preserves exact assertion error and stack, reports through stdout only", async () => {
  const original = new Error("private original");
  const stack = original.stack;
  const log = vi.spyOn(console, "log").mockImplementation(() => {});
  await expect(
    withComparisonFailureDiagnostic(page(["running"]), async () => {
      throw original;
    }),
  ).rejects.toBe(original);
  expect(original.message).toBe("private original");
  expect(original.stack).toBe(stack);
  expect(log).toHaveBeenCalledWith(
    comparisonDiagnosticPrefix +
      JSON.stringify({
        presence: "single",
        status: "running",
        error_code: null,
      }),
  );
});
it("never observes a passing assertion", async () => {
  const p = page([]);
  await withComparisonFailureDiagnostic(p, async () => {});
  expect(p.evaluate).not.toHaveBeenCalled();
});
it("snapshot and stdout failures cannot replace original failure", async () => {
  const original = new Error("original");
  const p = {
    evaluate: vi.fn(async () => {
      throw new Error("secret");
    }),
  } as unknown as Pick<Page, "evaluate">;
  await expect(
    withComparisonFailureDiagnostic(p, async () => {
      throw original;
    }),
  ).rejects.toBe(original);
  vi.spyOn(console, "log").mockImplementation(() => {
    throw new Error("secret");
  });
  await expect(
    withComparisonFailureDiagnostic(page(["failed"]), async () => {
      throw original;
    }),
  ).rejects.toBe(original);
});

it("bounds hung snapshots, preserves original failure and ignores late completion", async () => {
  vi.useFakeTimers();
  const log = vi.spyOn(console, "log").mockImplementation(() => {});
  const original = new Error("original");
  const stack = original.stack;
  let complete!: (value: unknown) => void;
  const p = {
    evaluate: vi.fn(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    ),
  } as unknown as Pick<Page, "evaluate">;
  const result = withComparisonFailureDiagnostic(p, async () => {
    throw original;
  });
  const checked = expect(result).rejects.toBe(original);
  await vi.advanceTimersByTimeAsync(250);
  await checked;
  expect(p.evaluate).toHaveBeenCalledTimes(1);
  expect(original.message).toBe("original");
  expect(original.stack).toBe(stack);
  expect(vi.getTimerCount()).toBe(0);
  expect(log).not.toHaveBeenCalled();
  complete({ presence: "single", status: "failed", error_code: null });
  await Promise.resolve();
  await Promise.resolve();
  expect(log).not.toHaveBeenCalled();
});
it("clears the observation ceiling after quick success or rejection", async () => {
  vi.useFakeTimers();
  vi.spyOn(console, "log").mockImplementation(() => {});
  const original = new Error("original");
  for (const p of [
    page(["failed"]),
    {
      evaluate: vi.fn(async () => {
        throw new Error("private");
      }),
    } as unknown as Pick<Page, "evaluate">,
  ]) {
    await expect(
      withComparisonFailureDiagnostic(p, async () => {
        throw original;
      }),
    ).rejects.toBe(original);
    expect(vi.getTimerCount()).toBe(0);
  }
});
