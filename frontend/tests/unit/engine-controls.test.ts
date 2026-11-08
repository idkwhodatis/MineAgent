import type { ReactElement } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import type { EngineDetail } from "@/lib/engine-runs-api";
const mocks = vi.hoisted(() => ({ cancel: vi.fn(), setState: vi.fn() }));
vi.mock("react", async (original) => ({
  ...(await original<typeof import("react")>()),
  useState: (value: unknown) => [value, mocks.setState],
  useRef: (current: unknown) => ({ current }),
}));
vi.mock("@/lib/engine-submission-api", () => ({
  cancelEngineRun: mocks.cancel,
  engineRunActive: () => true,
}));
import { EngineControls } from "@/features/engine-controls";
function button(reload: () => Promise<unknown>) {
  const node = EngineControls({
    run: {
      id: "run",
      status: "running",
      actions: { cancel: true },
    } as EngineDetail,
    reload,
  });
  return node.props.children[0] as ReactElement<{ onClick: () => void }>;
}
beforeEach(() => vi.clearAllMocks());
it("admits one cancellation until both the response and status refresh settle", async () => {
  let resolve!: (value: unknown) => void;
  mocks.cancel.mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  let refresh!: () => void;
  const reload = vi.fn(
    () =>
      new Promise<void>((done) => {
        refresh = done;
      }),
  );
  const control = button(reload);
  control.props.onClick();
  control.props.onClick();
  expect(mocks.cancel).toHaveBeenCalledTimes(1);
  resolve({ pending: true });
  await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
  control.props.onClick();
  expect(mocks.cancel).toHaveBeenCalledTimes(1);
  refresh();
  await vi.waitFor(() =>
    expect(mocks.setState).toHaveBeenLastCalledWith(false),
  );
});
it("retains cancellation failure when the reconciliation read also fails", async () => {
  const failure = new Error("cancel response lost");
  mocks.cancel.mockRejectedValue(failure);
  const reload = vi.fn().mockRejectedValue(new Error("status unavailable"));
  button(reload).props.onClick();
  await vi.waitFor(() =>
    expect(mocks.setState).toHaveBeenLastCalledWith(false),
  );
  expect(mocks.setState).toHaveBeenCalledWith(failure);
  const update = mocks.setState.mock.calls.find(
    ([value]) => typeof value === "function",
  )![0];
  expect(update(failure)).toBe(failure);
  expect(reload).toHaveBeenCalledTimes(1);
});
it("does not retry a failed status refresh as if cancellation failed", async () => {
  mocks.cancel.mockResolvedValue({ pending: true });
  const failure = new Error("status unavailable");
  const reload = vi.fn().mockRejectedValue(failure);
  button(reload).props.onClick();
  await vi.waitFor(() =>
    expect(mocks.setState).toHaveBeenLastCalledWith(false),
  );
  expect(reload).toHaveBeenCalledTimes(1);
  const update = mocks.setState.mock.calls.find(
    ([value]) => typeof value === "function",
  )![0];
  expect(update(null)).toBe(failure);
});
