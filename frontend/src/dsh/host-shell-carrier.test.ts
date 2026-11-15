import {webcrypto, createHash} from "node:crypto";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {createHostShellCarrier, type DshShellHostApi} from "./host-shell-carrier";

const RESOURCE_ID = "7d04da09-2aa2-4912-a737-ad04f9372ccd";
const DIGEST = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
const bytes = new TextEncoder().encode("window.__dsh = 'ice';");

function deferred<T = unknown>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function fixture(overrides: Partial<DshShellHostApi> = {}) {
  const api: DshShellHostApi = {
    dsh_client_boot: vi.fn(async () => ({injections: []})),
    dsh_client_rpc: vi.fn(async request => ({ok: true, value: request.payload})),
    dsh_client_request_cancel: vi.fn(async () => ({cancelled: true})),
    dsh_client_stream_open: vi.fn(async request => ({stream_id: request.stream_id, cursor: 0})),
    dsh_client_stream_read: vi.fn(async () => ({items: [], cursor: 0, done: true})),
    dsh_client_stream_send: vi.fn(async request => ({sequence: request.sequence})),
    dsh_client_stream_cancel: vi.fn(async () => ({cancelled: true})),
    dsh_client_bundle: vi.fn(async () => ({
      status: 200, contentType: "text/javascript",
      resource: {resource_id: RESOURCE_ID, byte_length: bytes.length, sha256: DIGEST(bytes)},
    })),
    dsh_client_resource_read: vi.fn(async request => {
      const body = bytes.slice(request.offset, request.offset + request.length);
      return {
        resource_id: request.resource_id, offset: request.offset,
        body_base64: Buffer.from(body).toString("base64"), eof: request.offset + body.length === bytes.length,
      };
    }),
    dsh_client_resource_release: vi.fn(async () => ({released: true})),
    ...overrides,
  };
  return api;
}

beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => vi.unstubAllGlobals());

describe("desktop Host ShellCarrierPort", () => {
  it("accepts JSON-serialized public Remote<void> settlement without a value member",async()=>{
    const api=fixture({dsh_client_rpc:vi.fn(async()=>JSON.parse(JSON.stringify({ok:true,value:undefined})))}),port=createHostShellCarrier(api);
    await expect(port.unary({channel:"/api",endpoint:"$events/result",payload:{args:{}}})).resolves.toEqual({ok:true,value:undefined});expect(api.dsh_client_rpc).toHaveBeenCalledOnce();await port.dispose();
  });
  it("forwards bootstrap and DSH unary results without projecting chat or retrying", async () => {
    const api = fixture();
    const port = createHostShellCarrier(api);
    await expect(port.boot()).resolves.toEqual({injections: []});
    const payload = {sessionId: "session-1", args: {questionId: "question-1"}};
    await expect(port.unary({channel: "/api", endpoint: "session/read", payload})).resolves.toEqual({ok: true, value: payload});
    expect(api.dsh_client_rpc).toHaveBeenCalledExactlyOnceWith({
      request_id: expect.stringMatching(/^[\da-f-]{36}$/), channel: "/api", endpoint: "session/read", payload,
    });
    const error = {code: "remote-access/denied", message: "Denied", details: {reason: "scope"}};
    vi.mocked(api.dsh_client_rpc).mockResolvedValueOnce({ok: false, error});
    await expect(port.unary({channel: "/api", endpoint: "session/send", payload: {}})).resolves.toEqual({ok: false, error});
    await port.dispose();
  });

  it("interrupts pending unary and requests precise Host cancellation", async () => {
    const pending = deferred();
    const api = fixture({dsh_client_rpc: vi.fn(() => pending.promise)});
    const controller = new AbortController();
    const port = createHostShellCarrier(api);
    const reply = port.unary({channel: "/api", endpoint: "session/read", payload: {}}, controller.signal);
    const check = expect(reply).rejects.toMatchObject({name: "AbortError"});
    controller.abort();
    await check;
    await port.dispose();
    expect(api.dsh_client_request_cancel).toHaveBeenCalledExactlyOnceWith({
      request_id: vi.mocked(api.dsh_client_rpc).mock.calls[0]![0].request_id,
    });
    pending.resolve({ok: true, value: "late"});
  });

  it("preserves batch order and delivers a terminal error after its final items", async () => {
    const api = fixture();
    vi.mocked(api.dsh_client_stream_read)
      .mockResolvedValueOnce({items: [{type: "delta", text: "one"}], cursor: 1, done: false})
      .mockResolvedValueOnce({items: [{type: "delta", text: "two"}], cursor: 2, done: true,
        error: {code: "provider/disconnected", message: "Stream disconnected", details: {retryable: false}}});
    const port = createHostShellCarrier(api);
    const iterator = port.stream({channel: "/api", endpoint: "session/subscribe", payload: {}}, new AbortController().signal)[Symbol.asyncIterator]();
    await expect(iterator.next()).resolves.toEqual({value: {type: "delta", text: "one"}, done: false});
    await expect(iterator.next()).resolves.toEqual({value: {type: "delta", text: "two"}, done: false});
    await expect(iterator.next()).rejects.toMatchObject({code: "provider/disconnected", details: {retryable: false}});
    expect(vi.mocked(api.dsh_client_stream_read).mock.calls.map(([request]) => request.cursor)).toEqual([0, 1]);
    await port.dispose();
    expect(api.dsh_client_stream_cancel).toHaveBeenCalledTimes(1);
  });

  it("does not acknowledge a partially consumed batch", async () => {
    const api = fixture();
    vi.mocked(api.dsh_client_stream_read).mockResolvedValueOnce({items: ["first", "second"], cursor: 2, done: false});
    const port = createHostShellCarrier(api);
    const iterator = port.stream({channel: "/api", endpoint: "session/subscribe", payload: {}}, new AbortController().signal)[Symbol.asyncIterator]();
    await expect(iterator.next()).resolves.toMatchObject({value: "first"});
    await iterator.return?.();
    await port.dispose();
    expect(api.dsh_client_stream_read).toHaveBeenCalledTimes(1);
  });

  it("cancels a pending read immediately on iterator.return rather than waiting for Host output", async () => {
    const pending = deferred();
    const reading = deferred<void>();
    const api = fixture({dsh_client_stream_read: vi.fn(() => { reading.resolve(); return pending.promise; })});
    const port = createHostShellCarrier(api);
    const iterator = port.stream({channel: "/api", endpoint: "session/subscribe", payload: {}}, new AbortController().signal)[Symbol.asyncIterator]();
    const next = iterator.next();
    const check = expect(next).rejects.toMatchObject({name: "AbortError"});
    await reading.promise;
    await expect(iterator.return?.()).resolves.toMatchObject({done: true});
    await check;
    await port.dispose();
    expect(api.dsh_client_stream_cancel).toHaveBeenCalledTimes(1);
  });

  it("applies uplink backpressure and sends EOS only after its final acknowledged item", async () => {
    const ack = deferred();
    const sent = deferred<void>();
    const downstream = deferred();
    const api = fixture({dsh_client_stream_read: vi.fn(() => downstream.promise)});
    vi.mocked(api.dsh_client_stream_send).mockImplementationOnce(() => { sent.resolve(); return ack.promise; });
    let advanced = 0;
    const uplink = (async function* () { advanced++; yield "a"; advanced++; yield "b"; })();
    const port = createHostShellCarrier(api);
    const iterator = port.stream({channel: "/api", endpoint: "session/upload", payload: {}, uplink}, new AbortController().signal)[Symbol.asyncIterator]();
    const next = iterator.next();
    await sent.promise;
    expect(advanced).toBe(1);
    expect(api.dsh_client_stream_send).toHaveBeenCalledTimes(1);
    ack.resolve({sequence: 0});
    await vi.waitFor(() => expect(api.dsh_client_stream_send).toHaveBeenCalledTimes(3));
    expect(vi.mocked(api.dsh_client_stream_send).mock.calls.map(([request]) => [request.sequence, request.items, request.done]))
      .toEqual([[0, ["a"], false], [1, ["b"], false], [2, [], true]]);
    downstream.resolve({items: [], cursor: 0, done: true});
    await expect(next).resolves.toMatchObject({done: true});
    await port.dispose();
  });

  it("keeps concurrent stream identities and cancellation separate", async () => {
    const pending = deferred();
    const reads = deferred<void>();
    const api = fixture({dsh_client_stream_read: vi.fn(() => {
      if (vi.mocked(api.dsh_client_stream_read).mock.calls.length === 2) reads.resolve();
      return pending.promise;
    })});
    const port = createHostShellCarrier(api);
    const a = new AbortController();
    const b = new AbortController();
    const request = {channel: "/api", endpoint: "session/subscribe", payload: {}};
    const anext = port.stream(request, a.signal)[Symbol.asyncIterator]().next();
    const bnext = port.stream(request, b.signal)[Symbol.asyncIterator]().next();
    const acheck = expect(anext).rejects.toMatchObject({name: "AbortError"});
    await reads.promise;
    a.abort();
    await acheck;
    expect(api.dsh_client_stream_cancel).toHaveBeenCalledTimes(1);
    const ids = vi.mocked(api.dsh_client_stream_open).mock.calls.map(([row]) => row.stream_id);
    expect(ids[0]).not.toBe(ids[1]);
    expect(api.dsh_client_stream_cancel).toHaveBeenCalledWith({stream_id: ids[0]});
    pending.resolve({items: [], cursor: 0, done: true});
    await expect(bnext).resolves.toMatchObject({done: true});
    await port.dispose();
  });

  it("propagates process loss and malformed cursors without a replay", async () => {
    const api = fixture();
    vi.mocked(api.dsh_client_stream_read).mockRejectedValueOnce(new Error("native_process_exited"));
    const port = createHostShellCarrier(api);
    const request = {channel: "/api", endpoint: "session/subscribe", payload: {}};
    const signal = new AbortController().signal;
    await expect(port.stream(request, signal)[Symbol.asyncIterator]().next()).rejects.toThrow("native_process_exited");
    vi.mocked(api.dsh_client_stream_read).mockResolvedValueOnce({items: [1], cursor: 0, done: false});
    await expect(port.stream(request, signal)[Symbol.asyncIterator]().next()).rejects.toThrow("batch_invalid");
    await port.dispose();
    expect(api.dsh_client_stream_open).toHaveBeenCalledTimes(2);
    expect(api.dsh_client_stream_read).toHaveBeenCalledTimes(2);
  });

  it("reads bundle pages by exact offsets, verifies SHA, and releases the resource", async () => {
    const api = fixture();
    const port = createHostShellCarrier(api, {resourceChunkBytes: 7});
    const bundle = await port.fetchBundle("/plugins/a.js?rev=1");
    expect([...bundle.body]).toEqual([...bytes]);
    expect(vi.mocked(api.dsh_client_resource_read).mock.calls.map(([request]) => [request.offset, request.length]))
      .toEqual([[0, 7], [7, 7], [14, bytes.length - 14]]);
    await port.dispose();
    expect(api.dsh_client_resource_release).toHaveBeenCalledExactlyOnceWith({resource_id: RESOURCE_ID});
  });

  it("rejects oversized bundles before reading and releases their resource", async () => {
    const api = fixture();
    const port = createHostShellCarrier(api, {maxBundleBytes: 1});
    await expect(port.fetchBundle("/plugins/a.js?rev=1")).rejects.toThrow("bundle_too_large");
    await port.dispose();
    expect(api.dsh_client_resource_read).not.toHaveBeenCalled();
    expect(api.dsh_client_resource_release).toHaveBeenCalledTimes(1);
  });

  it.each(["digest", "offset", "resource", "eof", "base64"])("rejects corrupted %s and releases without retry", async mode => {
    const api = fixture();
    vi.mocked(api.dsh_client_resource_read).mockImplementation(async request => {
      const row = {resource_id: RESOURCE_ID, offset: request.offset, body_base64: Buffer.from(bytes).toString("base64"), eof: true};
      return {...row, ...{
        digest: {body_base64: Buffer.from(new Uint8Array(bytes.length)).toString("base64")},
        offset: {offset: 1}, resource: {resource_id: crypto.randomUUID()}, eof: {eof: false}, base64: {body_base64: "!bad"},
      }[mode]};
    });
    const port = createHostShellCarrier(api);
    await expect(port.fetchBundle("/plugins/a.js?rev=1")).rejects.toThrow(mode === "digest" ? "digest_mismatch" : "chunk_invalid");
    await port.dispose();
    expect(api.dsh_client_resource_read).toHaveBeenCalledTimes(1);
    expect(api.dsh_client_resource_release).toHaveBeenCalledTimes(1);
  });

  it("does not load a missing bundle or retry a failed Host request", async () => {
    const api = fixture({dsh_client_bundle: vi.fn(async () => ({status: 404, contentType: null}))});
    const port = createHostShellCarrier(api);
    await expect(port.fetchBundle("/plugins/missing.js")).resolves.toEqual({status: 404, contentType: null, body: new Uint8Array()});
    expect(api.dsh_client_resource_read).not.toHaveBeenCalled();
    vi.mocked(api.dsh_client_rpc).mockRejectedValueOnce(new Error("host_unavailable"));
    await expect(port.unary({channel: "/api", endpoint: "session/read", payload: {}})).rejects.toThrow("host_unavailable");
    await port.dispose();
    expect(api.dsh_client_rpc).toHaveBeenCalledTimes(1);
    await expect(port.boot()).rejects.toMatchObject({name: "AbortError"});
  });

  it("stops a pending uplink when disposed and reports cleanup failure without retry", async () => {
    const pending = deferred<IteratorResult<unknown>>();
    const reading = deferred<void>();
    const returned = vi.fn(async () => ({done: true as const, value: undefined}));
    const observe = vi.fn();
    const api = fixture({
      dsh_client_stream_read: vi.fn(() => { reading.resolve(); return new Promise(() => undefined); }),
      dsh_client_stream_cancel: vi.fn(async () => { throw new Error("host_lost"); }),
    });
    const port = createHostShellCarrier(api, {onCleanupFailure: observe});
    const uplink = {[Symbol.asyncIterator]: () => ({next: () => pending.promise, return: returned})};
    const iterator = port.stream({channel: "/api", endpoint: "session/upload", payload: {}, uplink}, new AbortController().signal)[Symbol.asyncIterator]();
    const check = expect(iterator.next()).rejects.toMatchObject({name: "AbortError"});
    await reading.promise;
    await port.dispose();
    await check;
    expect(returned).toHaveBeenCalledTimes(1);
    expect(observe).toHaveBeenCalledWith(expect.objectContaining({message: "host_lost"}));
    expect(api.dsh_client_stream_cancel).toHaveBeenCalledTimes(1);
    expect(api.dsh_client_stream_send).not.toHaveBeenCalled();
  });

  it("releases a bundle lease published after the view was disposed", async () => {
    const pending = deferred();
    const api = fixture({dsh_client_bundle: vi.fn(() => pending.promise)});
    const port = createHostShellCarrier(api);
    const check = expect(port.fetchBundle("/plugins/a.js?rev=1")).rejects.toMatchObject({name: "AbortError"});
    await port.dispose();
    await check;
    pending.resolve({status: 200, contentType: "text/javascript",
      resource: {resource_id: RESOURCE_ID, byte_length: bytes.length, sha256: DIGEST(bytes)}});
    await vi.waitFor(() => expect(api.dsh_client_resource_release).toHaveBeenCalledExactlyOnceWith({resource_id: RESOURCE_ID}));
    expect(api.dsh_client_resource_read).not.toHaveBeenCalled();
  });

  it("cancels a read during a paged bundle transfer and releases the resource", async () => {
    const reading = deferred<void>();
    const api = fixture({dsh_client_resource_read: vi.fn(() => { reading.resolve(); return new Promise(() => undefined); })});
    const port = createHostShellCarrier(api);
    const check = expect(port.fetchBundle("/plugins/a.js?rev=1")).rejects.toMatchObject({name: "AbortError"});
    await reading.promise;
    await port.dispose();
    await check;
    await vi.waitFor(() => expect(api.dsh_client_resource_release).toHaveBeenCalledTimes(1));
  });
});
