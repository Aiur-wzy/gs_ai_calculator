import type {ClientTransportHooks} from "@deepseek-ai/dsh-client-connection/client";

/**
 * Authority-neutral shell carrier for the DSH client composition spike.
 *
 * This module deliberately has no DSH package import while W0 qualifies the
 * target cohort.  Its return value is structurally compatible with the public
 * `ClientTransportHooks` shape exposed by `dsh-client-connection/client`:
 * `rpc`, `rpc.open`, `loadBundle`, and `ownsHost`.
 *
 * It does not own Session, Turn, message, question, retry, or UI state.  A
 * production host will implement ShellCarrierPort over the authenticated
 * private Named Pipe and Node's public `clientModules.fetchBundle(Request)`.
 */

export type ShellCarrierFailure = Readonly<{
  code: string;
  message: string;
  details: Readonly<Record<string, unknown>>;
}>;

export type ShellCarrierResult<T = unknown> =
  | Readonly<{ok: true; value: T}>
  | Readonly<{ok: false; error: ShellCarrierFailure}>;

export type ShellUnaryRequest = Readonly<{
  channel: string;
  endpoint: string;
  payload: unknown;
}>;

export type ShellStreamRequest = ShellUnaryRequest & Readonly<{
  uplink?: AsyncIterable<unknown>;
}>;

export type ShellBundleResponse = Readonly<{
  status: number;
  contentType: string | null;
  body: Uint8Array;
}>;

/** Host operations required by the browser carrier; no application state. */
export interface ShellCarrierPort {
  unary(request: ShellUnaryRequest, signal?: AbortSignal): Promise<ShellCarrierResult>;
  stream(request: ShellStreamRequest, signal: AbortSignal): AsyncIterable<unknown>;
  fetchBundle(url: string): Promise<ShellBundleResponse>;
}

/**
 * Executes a Host-supplied, revision-addressed DSH client bundle.
 *
 * Execution is intentionally injected.  The carrier never falls back to
 * `eval`, `Function`, a network script URL, or an unreviewed global loader.
 */
export interface ShellBundleExecutor {
  execute(input: Readonly<{url: string; source: string}>): Promise<void> | void;
}

export type DshShellTransport = Readonly<{
  rpc: NonNullable<ClientTransportHooks["rpc"]> & Readonly<{
    open: NonNullable<NonNullable<ClientTransportHooks["rpc"]>["open"]>;
  }>;
  loadBundle: NonNullable<ClientTransportHooks["loadBundle"]>;
  ownsHost?: true;
}>;

export type ShellCarrierOptions = Readonly<{
  maxBundleBytes?: number;
  /** Set only after the production shell proves that no other principal can reach this Host. */
  ownsHost?: boolean;
}>;

const JAVASCRIPT_CONTENT_TYPE = /^(?:text|application)\/(?:javascript|x-javascript)(?:\s*;|$)/i;
const DEFAULT_MAX_BUNDLE_BYTES = 128 * 1024 * 1024;
const CONTROL_CHARACTER = /[\u0000-\u001f\u007f]/;

function assertTarget(channel: string, endpoint: string): void {
  const channelSegments = channel.slice(1).split("/");
  const endpointSegments = endpoint.split("/");
  if (
    !channel.startsWith("/")
    || channel.startsWith("//")
    || channel.includes("\\")
    || channel.includes("?")
    || channel.includes("#")
    || CONTROL_CHARACTER.test(channel)
    || channelSegments.some(segment => segment === "" || segment === "." || segment === "..")
    || endpoint === ""
    || endpoint.startsWith("/")
    || endpoint.includes("\\")
    || endpoint.includes("?")
    || endpoint.includes("#")
    || CONTROL_CHARACTER.test(endpoint)
    || endpointSegments.some(segment => segment === "" || segment === "." || segment === "..")
  ) {
    throw new Error(`dsh_shell_carrier_invalid_target:${channel}/${endpoint}`);
  }
}

function normalizeBundleUrl(input: string): string {
  if (
    input === ""
    || CONTROL_CHARACTER.test(input)
    || input.includes("\\")
    || input.includes("#")
    || input.startsWith("//")
    || /^[A-Za-z][A-Za-z\d+.-]*:/.test(input)
  ) {
    throw new Error("dsh_shell_carrier_bundle_url_invalid");
  }

  const queryIndex = input.indexOf("?");
  const rawPath = queryIndex === -1 ? input : input.slice(0, queryIndex);
  const absolutePath = rawPath.startsWith("/") ? rawPath : `/${rawPath}`;
  if (!absolutePath.startsWith("/plugins/")) throw new Error("dsh_shell_carrier_bundle_url_invalid");

  const segments = absolutePath.slice(1).split("/");
  for (const [index, rawSegment] of segments.entries()) {
    const isAllowedComboRootTerminator = index === segments.length - 1 && rawSegment === "" && segments.length === 2;
    if (rawSegment === "" && !isAllowedComboRootTerminator) throw new Error("dsh_shell_carrier_bundle_url_invalid");
    let decoded = rawSegment;
    try {
      for (;;) {
        const next = decodeURIComponent(decoded);
        if (next === decoded) break;
        decoded = next;
      }
    } catch {
      throw new Error("dsh_shell_carrier_bundle_url_invalid");
    }
    if (
      decoded === "."
      || decoded === ".."
      || decoded.includes("/")
      || decoded.includes("\\")
      || CONTROL_CHARACTER.test(decoded)
    ) {
      throw new Error("dsh_shell_carrier_bundle_url_invalid");
    }
  }

  const absoluteInput = input.startsWith("/") ? input : `/${input}`;
  const url = new URL(absoluteInput, "https://dsh-shell.invalid");
  if (
    url.origin !== "https://dsh-shell.invalid"
    || !url.pathname.startsWith("/plugins/")
    || url.username !== ""
    || url.password !== ""
    || url.hash !== ""
  ) {
    throw new Error("dsh_shell_carrier_bundle_url_invalid");
  }
  return `${url.pathname}${url.search}`;
}

function abortError(signal: AbortSignal): Error {
  if (signal.reason instanceof Error) return signal.reason;
  return new DOMException("The operation was aborted", "AbortError");
}

function abortableStream(source: AsyncIterable<unknown>, signal: AbortSignal): AsyncIterable<unknown> {
  return {
    [Symbol.asyncIterator](): AsyncIterator<unknown> {
      const iterator = source[Symbol.asyncIterator]();
      let settled = false;

      const close = async (): Promise<void> => {
        if (settled) return;
        settled = true;
        await iterator.return?.();
      };

      return {
        async next(): Promise<IteratorResult<unknown>> {
          if (signal.aborted) {
            await close();
            throw abortError(signal);
          }
          const result = await iterator.next();
          if (result.done) settled = true;
          if (signal.aborted) {
            await close();
            throw abortError(signal);
          }
          return result;
        },
        async return(value?: unknown): Promise<IteratorResult<unknown>> {
          await close();
          return {done: true, value};
        },
        async throw(error?: unknown): Promise<IteratorResult<unknown>> {
          settled = true;
          if (iterator.throw) return iterator.throw(error);
          await iterator.return?.();
          throw error;
        },
      };
    },
  };
}

/** Build the transport object consumed by DSH's public Connection installer. */
export function createDshShellTransport(
  port: ShellCarrierPort,
  executor: ShellBundleExecutor,
  options: ShellCarrierOptions = {},
): DshShellTransport {
  const maximum = options.maxBundleBytes ?? DEFAULT_MAX_BUNDLE_BYTES;
  if (!Number.isSafeInteger(maximum) || maximum <= 0) throw new Error("dsh_shell_carrier_bundle_limit_invalid");

  const rpc = Object.freeze({
    async call(
      channel: string,
      endpoint: string,
      payload: unknown,
      signal?: AbortSignal,
    ): Promise<ShellCarrierResult> {
      assertTarget(channel, endpoint);
      signal?.throwIfAborted();
      const result = await port.unary(Object.freeze({channel, endpoint, payload}), signal);
      signal?.throwIfAborted();
      return result;
    },

    open(
      channel: string,
      endpoint: string,
      payload: unknown,
      signal: AbortSignal,
      uplink?: AsyncIterable<unknown>,
    ): AsyncIterable<unknown> {
      assertTarget(channel, endpoint);
      if (channel !== "/api") throw new Error("dsh_shell_carrier_stream_channel_invalid");
      if (signal.aborted) {
        return abortableStream({
          async *[Symbol.asyncIterator]() {
            return;
          },
        }, signal);
      }
      const request = Object.freeze({
        channel,
        endpoint,
        payload,
        ...(uplink === undefined ? {} : {uplink}),
      });
      return abortableStream(port.stream(request, signal), signal);
    },
  });

  return Object.freeze({
    rpc,
    async loadBundle(input: string): Promise<void> {
      const url = normalizeBundleUrl(input);
      const response = await port.fetchBundle(url);
      if (response.status !== 200) throw new Error(`dsh_shell_carrier_bundle_http_${response.status}`);
      if (!JAVASCRIPT_CONTENT_TYPE.test(response.contentType ?? "")) {
        throw new Error("dsh_shell_carrier_bundle_content_type_invalid");
      }
      if (response.body.byteLength > maximum) throw new Error("dsh_shell_carrier_bundle_too_large");
      const source = new TextDecoder("utf-8", {fatal: true}).decode(response.body);
      await executor.execute(Object.freeze({url, source}));
    },
    ...(options.ownsHost === true ? {ownsHost: true as const} : {}),
  });
}

