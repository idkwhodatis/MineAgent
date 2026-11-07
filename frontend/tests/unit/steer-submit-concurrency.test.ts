import { expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ request: vi.fn(), invalidate: vi.fn() }));
vi.mock("react", async (original) => ({
  ...(await original<typeof import("react")>()),
  useState: (value: unknown) => [value, vi.fn()],
  useRef: (current: unknown) => ({ current }),
}));
vi.mock("@tanstack/react-query", async (original) => ({
  ...(await original<typeof import("@tanstack/react-query")>()),
  useQuery: vi.fn(),
  useQueryClient: () => ({ invalidateQueries: mocks.invalidate }),
}));
vi.mock("@/lib/factor-api", () => ({ factorRequest: mocks.request }));
import { SteerEditor } from "@/features/rd-steers";
it("sends only one create when the form submits twice before React renders pending", async () => {
  let resolve!: (value: unknown) => void;
  mocks.request.mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  mocks.invalidate.mockResolvedValue(undefined);
  const close = vi.fn();
  const editor = SteerEditor({ close });
  const event = { preventDefault: vi.fn() };
  editor.props.onSubmit(event);
  editor.props.onSubmit(event);
  expect(mocks.request).toHaveBeenCalledTimes(1);
  resolve({});
  await vi.waitFor(() => expect(close).toHaveBeenCalledTimes(1));
});
