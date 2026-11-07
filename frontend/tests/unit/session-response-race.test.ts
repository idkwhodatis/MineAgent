import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { readSession, saveSession } from "../../src/lib/session";
import { retainExternalInput } from "../../src/lib/research-input-intake";
import { factorRequest } from "../../src/lib/factor-api";
import * as sdk from "../../src/client/sdk.gen";
import { client } from "../../src/client/client.gen";
import "../../src/lib/api";

beforeEach(() => {
  const storage = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  });
  vi.stubGlobal("window", { dispatchEvent: vi.fn() });
  client.setConfig({ baseUrl: "https://workbench.example" });
  saveSession("old-session", 60);
});
afterEach(() => vi.unstubAllGlobals());

const requests = {
  generated: () => sdk.readMe(),
  intake: () => retainExternalInput("exact external input"),
  factor: () => factorRequest("/factors"),
};

it.each(Object.entries(requests))(
  "%s keeps a newer session when an older request returns 401",
  async (_kind, request) => {
    let finish!: (response: Response) => void;
    let started!: () => void;
    const sent = new Promise<void>((resolve) => {
      started = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: Request | string, init?: RequestInit) => {
        const headers =
          input instanceof Request ? input.headers : new Headers(init?.headers);
        expect(headers.get("Authorization")).toBe("Bearer old-session");
        started();
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      }),
    );
    const pending = request().catch((error: unknown) => error);
    await sent;
    saveSession("new-session", 60);
    finish(
      new Response(JSON.stringify({ code: "unauthorized" }), { status: 401 }),
    );
    await pending;
    expect(readSession()?.accessToken).toBe("new-session");
  },
);

it.each(Object.entries(requests))(
  "%s still expires the session whose request returned 401",
  async (_kind, request) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ code: "unauthorized" }), {
            status: 401,
          }),
      ),
    );
    await request().catch(() => undefined);
    expect(readSession()).toBeNull();
  },
);

it("does not expire an existing session for a rejected login", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ code: "unauthorized" }), { status: 401 }),
    ),
  );
  await sdk
    .loginAccessToken({
      body: { username: "test@example.com", password: "test-only" },
    })
    .catch(() => undefined);
  expect(readSession()?.accessToken).toBe("old-session");
});
