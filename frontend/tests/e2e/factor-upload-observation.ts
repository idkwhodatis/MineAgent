// Test-only, passive observation of the actual immutable File passed to fetch.
// CDP may omit File/Blob bytes; never replace the body or mock the transport.
type Observation = { keyMatches: boolean; size: number; bytes: number[] };
type State = { pending?: Promise<Observation>; overlap: boolean };
type ObservedGlobal = typeof globalThis & { __factorRawUpload?: State };

export function installFactorUploadObservation(options: {
  url: string;
  key: string;
  maximum: number;
}) {
  const scope = globalThis as ObservedGlobal;
  const nativeFetch = scope.fetch;
  const state: State = { overlap: false };
  scope.__factorRawUpload = state;
  scope.fetch = function (...args: Parameters<typeof fetch>) {
    const [input, init] = args;
    const url = new URL(
      input instanceof Request ? input.url : String(input),
      location.href,
    );
    const method =
      init?.method ?? (input instanceof Request ? input.method : "GET");
    if (url.href === options.url && method.toUpperCase() === "POST") {
      if (state.pending) state.overlap = true;
      const headers = new Headers(
        init?.headers ?? (input instanceof Request ? input.headers : undefined),
      );
      const body = init?.body;
      // This flow deliberately submits a File. Refuse unsupported observations,
      // not the real fetch: the test fails but production behavior is unchanged.
      state.pending = (async () => {
        if (
          !(body instanceof File) ||
          body.size < 1 ||
          body.size > options.maximum
        )
          throw new Error("bounded immutable upload observation unavailable");
        const raw = await body.arrayBuffer();
        if (raw.byteLength !== body.size)
          throw new Error("upload observation size drift");
        return {
          keyMatches: headers.get("idempotency-key") === options.key,
          size: raw.byteLength,
          bytes: Array.from(new Uint8Array(raw)),
        };
      })();
      // Prevent an unhandled rejection before the response-side test reads it.
      void state.pending.catch(() => undefined);
    }
    return Reflect.apply(nativeFetch, this, args);
  };
}

export async function takeFactorUploadObservation(): Promise<Observation> {
  const state = (globalThis as ObservedGlobal).__factorRawUpload;
  if (!state?.pending || state.overlap)
    throw new Error("missing or overlapping upload observation");
  try {
    return await state.pending;
  } finally {
    state.pending = undefined;
  }
}
