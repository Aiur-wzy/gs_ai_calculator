import type {DshClientBootPayload} from "./client-composition";
import type {
  ShellBundleResponse,
  ShellCarrierFailure,
  ShellCarrierPort,
  ShellCarrierResult,
  ShellStreamRequest,
  ShellUnaryRequest,
} from "./shell-carrier";

/** Private desktop Host contract. No Session or Turn state is stored here. */
export interface DshShellHostApi {
  dsh_client_boot(): Promise<unknown>;
  dsh_client_rpc(request: ShellUnaryRequest & {request_id: string}): Promise<unknown>;
  dsh_client_request_cancel(request: {request_id: string}): Promise<unknown>;
  dsh_client_stream_open(request: ShellUnaryRequest & {stream_id: string; has_uplink: boolean}): Promise<unknown>;
  dsh_client_stream_read(request: {stream_id: string; cursor: number}): Promise<unknown>;
  dsh_client_stream_send(request: {stream_id: string; sequence: number; items: unknown[]; done: boolean}): Promise<unknown>;
  dsh_client_stream_cancel(request: {stream_id: string}): Promise<unknown>;
  dsh_client_bundle(request: {url: string}): Promise<unknown>;
  dsh_client_resource_read(request: {resource_id: string; offset: number; length: number}): Promise<unknown>;
  dsh_client_resource_release(request: {resource_id: string}): Promise<unknown>;
}

export type HostShellCarrierOptions = Readonly<{
  maxBundleBytes?: number;
  /** Transfer chunk size, independent of all Agent and model budgets. */
  resourceChunkBytes?: number;
  onCleanupFailure?: (error: unknown) => void;
}>;

export interface HostShellCarrier extends ShellCarrierPort {
  boot(): Promise<DshClientBootPayload>;
  dispose(): Promise<void>;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256 = /^[0-9a-f]{64}$/;
const DEFAULT_MAX_BUNDLE_BYTES = 128 * 1024 * 1024;
const DEFAULT_CHUNK_BYTES = 1024 * 1024;

function record(value: unknown, code: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(code);
  return value as Record<string, unknown>;
}

function natural(value: unknown, code: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) throw new Error(code);
  return value as number;
}

function uuid(value: unknown, code: string): string {
  if (typeof value !== "string" || !UUID.test(value)) throw new Error(code);
  return value;
}

function failure(value: unknown): ShellCarrierFailure {
  const row = record(value, "dsh_shell_host_failure_invalid");
  if (typeof row.code !== "string" || typeof row.message !== "string") {
    throw new Error("dsh_shell_host_failure_invalid");
  }
  return {code: row.code, message: row.message, details: record(row.details, "dsh_shell_host_failure_invalid")};
}

function result(value: unknown): ShellCarrierResult {
  const row = record(value, "dsh_shell_host_result_invalid");
  // Public Remote<void> success serializes as {ok:true}: JSON omits an
  // undefined value (notably $events/result). Restore the JS value rather
  // than misclassifying a successful waterfall settlement as bad transport.
  if (row.ok === true) return {ok: true, value: row.value};
  if (row.ok === false) return {ok: false, error: failure(row.error)};
  throw new Error("dsh_shell_host_result_invalid");
}

export class DshShellHostStreamError extends Error {
  readonly code: string;
  readonly details: Readonly<Record<string, unknown>>;

  constructor(value: ShellCarrierFailure) {
    super(value.message);
    this.name = "DshShellHostStreamError";
    this.code = value.code;
    this.details = value.details;
  }
}

function abortReason(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException("The operation was aborted", "AbortError");
}

/** Interrupt a pending Host promise without waiting for its long poll to finish. */
function abortable<T>(pending: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(abortReason(signal));
  return new Promise<T>((resolve, reject) => {
    const aborted = () => reject(abortReason(signal));
    signal.addEventListener("abort", aborted, {once: true});
    pending.then(resolve, reject).finally(() => signal.removeEventListener("abort", aborted));
  });
}

function decodeBase64(value: unknown): Uint8Array {
  if (typeof value !== "string") throw new Error("dsh_shell_host_resource_chunk_invalid");
  try {
    const decoded = atob(value);
    if (btoa(decoded) !== value) throw new Error("noncanonical_base64");
    return Uint8Array.from(decoded, character => character.charCodeAt(0));
  } catch {
    throw new Error("dsh_shell_host_resource_chunk_invalid");
  }
}

/**
 * Thin pywebview adapter for DSH's public ClientTransportHooks carrier.
 *
 * Host long polls carry opaque DSH values. Cursors acknowledge only the last
 * consumed batch; the adapter never retries commands, replays a Turn, or
 * derives chat, question, approval or task state. Terminal errors are delivered
 * after all items in their batch. File bytes use leased paged resources so
 * base64 expansion cannot overflow the surrounding RPC envelope.
 */
export function createHostShellCarrier(api: DshShellHostApi, options: HostShellCarrierOptions = {}): HostShellCarrier {
  const maxBytes = options.maxBundleBytes ?? DEFAULT_MAX_BUNDLE_BYTES;
  const chunkBytes = options.resourceChunkBytes ?? DEFAULT_CHUNK_BYTES;
  if (!Number.isSafeInteger(maxBytes) || maxBytes <= 0 || !Number.isSafeInteger(chunkBytes)
    || chunkBytes <= 0 || chunkBytes > DEFAULT_CHUNK_BYTES) throw new Error("dsh_shell_host_capacity_invalid");

  const lifecycle = new AbortController();
  const cleanups = new Set<Promise<void>>();
  let disposePromise: Promise<void> | undefined;
  const cleanup = (work: () => Promise<unknown>): void => {
    const pending = Promise.resolve().then(work).then(() => undefined, error => {
      try { options.onCleanupFailure?.(error); } catch { /* observer cannot replace the original failure */ }
    });
    cleanups.add(pending);
    void pending.finally(() => cleanups.delete(pending));
  };
  const active = (signal?: AbortSignal): AbortSignal => signal
    ? AbortSignal.any([lifecycle.signal, signal])
    : lifecycle.signal;

  const port: HostShellCarrier = {
    async boot() {
      lifecycle.signal.throwIfAborted();
      const value = record(await abortable(api.dsh_client_boot(), lifecycle.signal), "dsh_shell_host_boot_invalid");
      if (!Array.isArray(value.injections)) throw new Error("dsh_shell_host_boot_invalid");
      // Exact graph and executable validation belongs to client-composition.
      return value as unknown as DshClientBootPayload;
    },

    async unary(request, signal) {
      const current = active(signal);
      current.throwIfAborted();
      const requestId = uuid(crypto.randomUUID(), "dsh_shell_host_request_id_invalid");
      const cancelled = () => cleanup(() => api.dsh_client_request_cancel({request_id: requestId}));
      current.addEventListener("abort", cancelled, {once: true});
      try {
        return result(await abortable(api.dsh_client_rpc({...request, request_id: requestId}), current));
      } finally {
        current.removeEventListener("abort", cancelled);
      }
    },

    stream(request: ShellStreamRequest, signal: AbortSignal): AsyncIterable<unknown> {
      return {
        [Symbol.asyncIterator]() {
          const controller = new AbortController();
          const current = active(AbortSignal.any([controller.signal, signal]));
          const streamId = uuid(crypto.randomUUID(), "dsh_shell_host_stream_id_invalid");
          let started = false;
          let cancellationSent = false;
          let input: AsyncIterator<unknown> | undefined;
          let inputClosed = false;
          const closeInput = () => {
            if (inputClosed || !input?.return) return;
            inputClosed = true;
            cleanup(() => Promise.resolve(input!.return!()));
          };
          const cancel = () => {
            closeInput();
            if (!started || cancellationSent) return;
            cancellationSent = true;
            cleanup(() => api.dsh_client_stream_cancel({stream_id: streamId}));
          };
          const run = async function* (): AsyncGenerator<unknown> {
            current.throwIfAborted();
            started = true;
            current.addEventListener("abort", cancel, {once: true});
            let pumping: Promise<void> | undefined;
            try {
              const opened = record(await abortable(api.dsh_client_stream_open({
                channel: request.channel, endpoint: request.endpoint, payload: request.payload,
                stream_id: streamId, has_uplink: request.uplink !== undefined,
              }), current), "dsh_shell_host_stream_open_invalid");
              if (uuid(opened.stream_id, "dsh_shell_host_stream_open_invalid") !== streamId
                || opened.cursor !== 0) throw new Error("dsh_shell_host_stream_open_invalid");

              if (request.uplink !== undefined) {
                input = request.uplink[Symbol.asyncIterator]();
                const source = input;
                pumping = (async () => {
                  let sequence = 0;
                  for (;;) {
                    const item = await abortable(Promise.resolve(source.next()), current);
                    current.throwIfAborted();
                    const ack = record(await abortable(api.dsh_client_stream_send({
                      stream_id: streamId, sequence, items: item.done ? [] : [item.value], done: item.done === true,
                    }), current), "dsh_shell_host_stream_uplink_ack_invalid");
                    if (ack.sequence !== sequence) throw new Error("dsh_shell_host_stream_uplink_ack_invalid");
                    sequence += 1;
                    if (item.done) return;
                  }
                })();
                void pumping.catch(error => controller.abort(error));
              }

              let cursor = 0;
              for (;;) {
                current.throwIfAborted();
                const batch = record(await abortable(api.dsh_client_stream_read({stream_id: streamId, cursor}), current),
                  "dsh_shell_host_stream_batch_invalid");
                const next = natural(batch.cursor, "dsh_shell_host_stream_cursor_invalid");
                if (!Array.isArray(batch.items) || typeof batch.done !== "boolean" || next < cursor
                  || (batch.items.length > 0 && next === cursor) || (batch.error !== undefined && !batch.done)) {
                  throw new Error("dsh_shell_host_stream_batch_invalid");
                }
                for (const item of batch.items) {
                  current.throwIfAborted();
                  yield item;
                }
                cursor = next;
                if (batch.error !== undefined) throw new DshShellHostStreamError(failure(batch.error));
                if (batch.done) return;
              }
            } finally {
              // return()/abort interrupt pending reads; they are not queued
              // behind an async generator's outstanding next().
              controller.abort();
              current.removeEventListener("abort", cancel);
              cancel();
              closeInput();
              await pumping?.catch(() => undefined);
            }
          };
          const iterator = run();
          return {
            next: () => iterator.next(),
            async return(value?: unknown) {
              controller.abort();
              cancel();
              // A pending next may reject because it was cancelled; its
              // consumer retains that rejection, while return settles cleanly.
              return iterator.return(value);
            },
            async throw(error?: unknown) {
              controller.abort(error);
              cancel();
              return iterator.throw(error);
            },
          };
        },
      };
    },

    async fetchBundle(url): Promise<ShellBundleResponse> {
      lifecycle.signal.throwIfAborted();
      const pending = api.dsh_client_bundle({url});
      // An in-flight publication may finish after this view is disposed. Its
      // lease must still be released, without retrying the bundle request.
      void pending.then(value => {
        if (!lifecycle.signal.aborted) return;
        const metadata = record(value, "dsh_shell_host_bundle_invalid");
        if (metadata.resource === undefined) return;
        const resource = record(metadata.resource, "dsh_shell_host_resource_invalid");
        const resourceId = uuid(resource.resource_id, "dsh_shell_host_resource_invalid");
        cleanup(() => api.dsh_client_resource_release({resource_id: resourceId}));
      }).catch(() => undefined);
      const metadata = record(await abortable(pending, lifecycle.signal), "dsh_shell_host_bundle_invalid");
      const status = natural(metadata.status, "dsh_shell_host_bundle_invalid");
      if (status < 100 || status > 599 || (metadata.contentType !== null && typeof metadata.contentType !== "string")) {
        throw new Error("dsh_shell_host_bundle_invalid");
      }
      if (status !== 200) return {status, contentType: metadata.contentType as string | null, body: new Uint8Array()};
      const resource = record(metadata.resource, "dsh_shell_host_resource_invalid");
      const resourceId = uuid(resource.resource_id, "dsh_shell_host_resource_invalid");
      try {
        const length = natural(resource.byte_length, "dsh_shell_host_resource_invalid");
        if (length > maxBytes) throw new Error("dsh_shell_host_bundle_too_large");
        if (typeof resource.sha256 !== "string" || !SHA256.test(resource.sha256)) throw new Error("dsh_shell_host_resource_invalid");
        const body = new Uint8Array(length);
        let offset = 0;
        while (offset < length) {
          lifecycle.signal.throwIfAborted();
          const requested = Math.min(chunkBytes, length - offset);
          const chunk = record(await abortable(api.dsh_client_resource_read({
            resource_id: resourceId, offset, length: requested,
          }), lifecycle.signal), "dsh_shell_host_resource_chunk_invalid");
          const bytes = decodeBase64(chunk.body_base64);
          if (chunk.resource_id !== resourceId || chunk.offset !== offset || bytes.byteLength === 0
            || bytes.byteLength > requested || chunk.eof !== (offset + bytes.byteLength === length)) {
            throw new Error("dsh_shell_host_resource_chunk_invalid");
          }
          body.set(bytes, offset);
          offset += bytes.byteLength;
        }
        const digest = await crypto.subtle.digest("SHA-256", body);
        const actual = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
        if (actual !== resource.sha256) throw new Error("dsh_shell_host_resource_digest_mismatch");
        lifecycle.signal.throwIfAborted();
        return {status, contentType: metadata.contentType as string | null, body};
      } finally {
        cleanup(() => api.dsh_client_resource_release({resource_id: resourceId}));
      }
    },

    dispose() {
      disposePromise ??= (async () => {
        lifecycle.abort(new DOMException("The desktop carrier was disposed", "AbortError"));
        // No implicit retry: retain any cleanup failure through the observer.
        await Promise.all([...cleanups]);
      })();
      return disposePromise;
    },
  };
  return Object.freeze(port);
}
