import { afterEach, expect, it, vi } from "vitest";
import {
  saveReviewedObservation,
  observationSteerText,
  watchObservationSession,
} from "../../src/lib/observation-steer";
import { factorRequest } from "../../src/lib/factor-api";
import { readSession, watchSession } from "../../src/lib/session";
vi.mock("../../src/lib/factor-api", () => ({ factorRequest: vi.fn() }));
vi.mock("../../src/lib/session", () => ({
  readSession: vi.fn(),
  watchSession: vi.fn(),
}));
function account(token = "owner-a") {
  vi.mocked(readSession).mockReturnValue({ accessToken: token } as ReturnType<
    typeof readSession
  >);
}
afterEach(() => vi.resetAllMocks());
it("saves exactly reviewed text without selecting a workflow or requesting release", async () => {
  account();
  const text = observationSteerText("Investigate", '{"stage_id":"retained"}');
  const result = { current: { kind: "direction", text, version_id: "saved" } };
  vi.mocked(factorRequest).mockResolvedValue(result);
  expect(
    await saveReviewedObservation(
      "Investigate",
      '{"stage_id":"retained"}',
      "owner-a",
    ),
  ).toBe(result);
  expect(factorRequest).toHaveBeenCalledExactlyOnceWith("/rd-steers", {
    method: "POST",
    body: { kind: "direction", text },
  });
});
it("rejects an old-account draft before any network request", async () => {
  account("owner-b");
  await expect(
    saveReviewedObservation("Investigate", "context", "owner-a"),
  ).rejects.toThrow("Account changed");
  expect(factorRequest).not.toHaveBeenCalled();
});
it("does not deliver previous-account save results into a new account", async () => {
  account();
  vi.mocked(factorRequest).mockImplementation(async () => {
    account("owner-b");
    return { current: { kind: "direction", text: "secret" } };
  });
  await expect(
    saveReviewedObservation("Investigate", "context", "owner-a"),
  ).rejects.toThrow("Account changed");
  expect(factorRequest).toHaveBeenCalledTimes(1);
});
it("does not resend uncertain writes or accept a mismatched saved version", async () => {
  account();
  vi.mocked(factorRequest).mockRejectedValue(new Error("uncertain transport"));
  await expect(
    saveReviewedObservation("Investigate", "context", "owner-a"),
  ).rejects.toThrow("uncertain");
  expect(factorRequest).toHaveBeenCalledTimes(1);
  vi.mocked(factorRequest).mockResolvedValue({
    current: { kind: "direction", text: "different" },
  });
  await expect(
    saveReviewedObservation("Investigate", "context", "owner-a"),
  ).rejects.toThrow("does not match");
});

it("revokes displayed context on session change and unsubscribes on unmount", () => {
  account();
  const revoke = vi.fn();
  const cleanup = vi.fn();
  let changed: () => void = () => {};
  vi.mocked(watchSession).mockImplementation((callback) => {
    changed = () => callback(new Event("session"));
    return cleanup;
  });
  const stop = watchObservationSession("owner-a", revoke);
  expect(revoke).not.toHaveBeenCalled();
  account("owner-b");
  changed();
  expect(revoke).toHaveBeenCalledTimes(1);
  stop();
  expect(cleanup).toHaveBeenCalledTimes(1);
});
