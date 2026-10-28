import { describe, expect, it, vi } from "vitest";
import {
  importStatusDiagnostic,
  observeFailedImport,
} from "../e2e/factor-import-status-diagnostic";

describe("bounded private import observation", () => {
  it("projects only fixed enums and booleans", () => {
    expect(
      importStatusDiagnostic(200, {
        status: "queued",
        generation: 1,
        error_code: "factor_busy",
        id: "private",
        archive_sha256: "private",
        path: "/private",
        error: "private",
        result_envelope_id: "private",
        local_bindings: { version_id: "private" },
      }),
    ).toBe(
      "FACTOR_IMPORT_STATUS_DIAGNOSTIC_V1:http=200;status=queued;error=factor_busy;generation=1;envelope=true;binding=true",
    );
  });
  it.each([
    null,
    [],
    "private",
    { status: "private", generation: 4, error_code: "private" },
    {
      status: "failed;id=private",
      generation: "1;private",
      result_envelope_id: {},
      local_bindings: [],
    },
    { generation: NaN },
    { generation: Infinity },
    { generation: -1 },
  ])("never serializes untrusted fields", (body) => {
    const value = importStatusDiagnostic("200;private", body);
    expect(value).not.toContain("private");
    expect(value).toMatch(
      /^FACTOR_IMPORT_STATUS_DIAGNOSTIC_V1:http=none;status=(unknown|queued|processing|succeeded|failed|blocked);error=(none|other);generation=unknown;envelope=false;binding=false$/,
    );
  });
  it("reads once and keeps HTTP and malformed JSON separate", async () => {
    const read = vi.fn(async () => ({
      status: () => 503,
      json: async () => {
        throw new Error("private");
      },
    }));
    expect(await observeFailedImport(read)).toContain(
      "http=503;status=unknown;error=none",
    );
    expect(read).toHaveBeenCalledTimes(1);
  });
  it("does not print thrown exceptions or retry the read", async () => {
    const read = vi.fn(async () => {
      throw new Error("private");
    });
    expect(await observeFailedImport(read)).toBe(
      importStatusDiagnostic(null, null),
    );
    expect(read).toHaveBeenCalledTimes(1);
  });
});
