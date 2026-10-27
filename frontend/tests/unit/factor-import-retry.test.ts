import { describe, expect, it } from "vitest";
import {
  retryFactorBusy,
  factorResponseCode,
} from "../e2e/factor-import-retry";

describe("real import acceptance busy retry bounds", () => {
  it("requires a real later response rather than treating busy as success", async () => {
    expect(
      factorResponseCode({ code: "database_unavailable", message: "private" }),
    ).toBe("database_unavailable");
    expect(factorResponseCode({ code: "factor_storage_unavailable" })).toBe(
      "factor_storage_unavailable",
    );
    expect(factorResponseCode({ code: "private-token" })).toBe("other");
    expect(factorResponseCode({ code: "factor_busy_private" })).toBe("other");
    expect(factorResponseCode(null)).toBe("none");
    let attempts = 0;
    const result = await retryFactorBusy(
      async () => {
        attempts++;
        return attempts === 1
          ? { status: 503, code: "factor_busy" }
          : { status: 202, code: undefined };
      },
      async () => {},
      () => 0,
    );
    expect(attempts).toBe(2);
    expect(result.status).toBe(202);
  });
  it("returns persistent busy as failure after at most four requests", async () => {
    let attempts = 0;
    const result = await retryFactorBusy(
      async () => {
        attempts++;
        return { status: 503, code: "factor_busy" };
      },
      async () => {},
      () => 0,
    );
    expect(attempts).toBe(4);
    expect(result.status).toBe(503);
  });
  it.each(["factor_capacity", "unauthorized", "factor_import_busy", undefined])(
    "does not retry unrelated failures %s",
    async (code) => {
      let attempts = 0;
      await retryFactorBusy(
        async () => {
          attempts++;
          return { status: 503, code };
        },
        async () => {},
        () => 0,
      );
      expect(attempts).toBe(1);
    },
  );
  it("does not reset the original five-second deadline", async () => {
    let clock = 0,
      attempts = 0;
    await expect(
      retryFactorBusy(
        async (remaining) => {
          expect(remaining).toBe(5000);
          attempts++;
          clock = 5001;
          return { status: 503, code: "factor_busy" };
        },
        async () => {},
        () => clock,
      ),
    ).rejects.toThrow("deadline exceeded");
    expect(attempts).toBe(1);
  });
});
