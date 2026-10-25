import { describe, expect, it } from "vitest";
import {
  factorSearch,
  factorValue,
  selectFactorSnapshot,
  selectFactorVersion,
} from "../../src/features/factor-workbench/selection";
const snapshot = "11111111-1111-4111-8111-111111111111";
describe("factor exact selection", () => {
  it("round-trips exact URL selection and bounded pagination", () => {
    expect(
      factorSearch({
        tab: "values",
        snapshot,
        listing: "ABC",
        date: "2026-10-23",
        offset: "50",
      }),
    ).toEqual({
      tab: "values",
      snapshot,
      listing: "ABC",
      date: "2026-10-23",
      offset: 50,
    });
  });
  it("rejects malformed identities, unsupported tabs, and unbounded cursors", () => {
    expect(
      factorSearch({
        tab: "sql",
        snapshot: "latest",
        listing: "x\n",
        date: "yesterday",
        offset: Infinity,
      }),
    ).toEqual({
      tab: undefined,
      snapshot: undefined,
      listing: undefined,
      date: undefined,
      offset: undefined,
    });
  });
  it("clears values and sample selection on physical snapshot or definition change", () => {
    const old = factorSearch({
      tab: "values",
      snapshot,
      listing: "ABC",
      date: "2026-10-23",
      offset: 50,
    });
    expect(selectFactorSnapshot(old)).toEqual({
      tab: "values",
      snapshot: undefined,
    });
    expect(selectFactorVersion(old)).toEqual({ tab: "values" });
  });
  it("never converts missing values to zero or discards real zero", () => {
    expect(factorValue(0, "valid")).toBe("0");
    expect(factorValue(null, "warmup", "insufficient_history")).toBe(
      "Unavailable · insufficient_history",
    );
    expect(factorValue(null, "valid")).toBe("Invalid value evidence");
    expect(factorValue(Infinity, "valid")).toBe("Invalid value evidence");
  });
});

import {
  factorArchiveHash,
  factorLibrarySearch,
  importStillRunning,
} from "../../src/features/factor-workbench/selection";
describe("factor import identity and bounded status", () => {
  it("retains an exact admission in the URL and rejects latest aliases", () => {
    expect(factorLibrarySearch({ import_id: snapshot })).toEqual({
      import_id: snapshot,
    });
    expect(factorLibrarySearch({ import_id: "latest" })).toEqual({
      import_id: undefined,
    });
  });
  it("polls only queued or processing, not blocked or failed admissions", () => {
    expect(importStillRunning("queued")).toBe(true);
    expect(importStillRunning("processing")).toBe(true);
    for (const status of [
      undefined,
      "blocked",
      "failed",
      "succeeded",
      "unknown",
    ])
      expect(importStillRunning(status)).toBe(false);
  });
  it("hashes exact bytes and rejects empty or oversized packages before upload", async () => {
    expect(await factorArchiveHash(new Blob(["abc"]))).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    await expect(factorArchiveHash(new Blob([]))).rejects.toThrow("nonempty");
    await expect(
      factorArchiveHash(new Blob([new Uint8Array(10_000_001)])),
    ).rejects.toThrow("10,000,000");
  });
});
