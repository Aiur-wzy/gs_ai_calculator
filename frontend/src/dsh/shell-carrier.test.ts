import {describe, expect, it, vi} from "vitest";
import {
  createDshShellTransport,
  type ShellBundleExecutor,
  type ShellCarrierPort,
  type ShellStreamRequest,
} from "./shell-carrier";

const encoder = new TextEncoder();

function fixture(overrides: Partial<ShellCarrierPort> = {}) {
  const port: ShellCarrierPort = {
    unary: vi.fn(async request => ({ok: true as const, value: request})),
    stream: vi.fn(async function* () {
      yield "first";
      yield "second";
    }),
    fetchBundle: vi.fn(async () => ({
      status: 200,
      contentType: "text/javascript; charset=utf-8",
      body: encoder.encode("window.__dshSpike = true;"),
    })),
    ...overrides,
  };
  const executor: ShellBundleExecutor = {execute: vi.fn(async () => undefined)};
  return {port, executor, transport: createDshShellTransport(port, executor)};
}

describe("DSH shell carrier spike", () => {
  it("forwards unary RPC without inventing another envelope", async () => {
    const {port, transport} = fixture();
    const payload = {sessionId: "session-1"};

    await expect(transport.rpc.call("/api", "session/read", payload)).resolves.toEqual({
      ok: true,
      value: {channel: "/api", endpoint: "session/read", payload},
    });
    expect(port.unary).toHaveBeenCalledTimes(1);
  });

  it("rejects malformed targets before they reach the host", async () => {
    const {port, transport} = fixture();
    await expect(transport.rpc.call("api", "session/read", {})).rejects.toThrow("invalid_target");
    await expect(transport.rpc.call("/api", "../session", {})).rejects.toThrow("invalid_target");
    expect(port.unary).not.toHaveBeenCalled();
  });

  it("does not impose a project-specific character whitelist on valid DSH endpoints", async () => {
    const {port, transport} = fixture();
    for (const endpoint of ["session/read", "settings/update", "goals/create", "$catalog/list:v2", "会话/读取"]) {
      await expect(transport.rpc.call("/api/v1", endpoint, {})).resolves.toMatchObject({ok: true});
    }
    expect(port.unary).toHaveBeenCalledTimes(5);
  });

  it("preserves stream terminal completion and forwards the uplink", async () => {
    let received: ShellStreamRequest | undefined;
    const uplink = (async function* () { yield {answer: 1}; })();
    const {transport} = fixture({
      stream: request => {
        received = request;
        return (async function* () {
          yield {type: "delta"};
          return;
        })();
      },
    });

    const output: unknown[] = [];
    for await (const value of transport.rpc.open("/api", "session/follow", {}, new AbortController().signal, uplink)) {
      output.push(value);
    }
    expect(output).toEqual([{type: "delta"}]);
    expect(received?.uplink).toBe(uplink);
  });

  it("restricts decoded streams to DSH's shared API channel", () => {
    const {port, transport} = fixture();
    expect(() => transport.rpc.open("/rpc", "session/follow", {}, new AbortController().signal)).toThrow("stream_channel_invalid");
    expect(port.stream).not.toHaveBeenCalled();
  });

  it("closes the source iterator when a stream is cancelled", async () => {
    const closed = vi.fn();
    const controller = new AbortController();
    const source: AsyncIterable<unknown> = {
      [Symbol.asyncIterator]() {
        let count = 0;
        return {
          async next() {
            count += 1;
            return {done: false, value: count};
          },
          async return() {
            closed();
            return {done: true, value: undefined};
          },
        };
      },
    };
    const {transport} = fixture({stream: () => source});
    const iterator = transport.rpc.open("/api", "session/follow", {}, controller.signal)[Symbol.asyncIterator]();

    await expect(iterator.next()).resolves.toEqual({done: false, value: 1});
    controller.abort();
    await expect(iterator.next()).rejects.toMatchObject({name: "AbortError"});
    expect(closed).toHaveBeenCalledTimes(1);
  });

  it("loads only revision-addressed plugin JavaScript and delegates controlled execution", async () => {
    const {port, executor, transport} = fixture();
    await transport.loadBundle("/plugins/?ids=%40deepseek-ai%2Fdsh-client-ui-chat&rev=abc");

    expect(port.fetchBundle).toHaveBeenCalledWith("/plugins/?ids=%40deepseek-ai%2Fdsh-client-ui-chat&rev=abc");
    expect(executor.execute).toHaveBeenCalledWith({
      url: "/plugins/?ids=%40deepseek-ai%2Fdsh-client-ui-chat&rev=abc",
      source: "window.__dshSpike = true;",
    });
  });

  it("normalizes documented relative combo, package and chunk bundle URLs", async () => {
    const {port, transport} = fixture();
    const rows = [
      "plugins/?ids=%40deepseek-ai%2Fdsh-client-ui-chat&rev=combo",
      "/plugins/@deepseek-ai/dsh-client-ui-chat/client.js?rev=single",
      "plugins/@deepseek-ai/dsh-client-ui-chat/chunk-ABC.js?rev=chunk",
    ];
    for (const row of rows) await transport.loadBundle(row);
    expect(port.fetchBundle).toHaveBeenNthCalledWith(1, "/plugins/?ids=%40deepseek-ai%2Fdsh-client-ui-chat&rev=combo");
    expect(port.fetchBundle).toHaveBeenNthCalledWith(2, "/plugins/@deepseek-ai/dsh-client-ui-chat/client.js?rev=single");
    expect(port.fetchBundle).toHaveBeenNthCalledWith(3, "/plugins/@deepseek-ai/dsh-client-ui-chat/chunk-ABC.js?rev=chunk");
  });

  it("rejects traversal, encoded separators, schemes, authorities and fragments", async () => {
    const {port, transport} = fixture();
    const invalid = [
      "plugins/../escape.js?rev=1",
      "plugins/%2e%2e/escape.js?rev=1",
      "plugins/%252e%252e/escape.js?rev=1",
      "plugins/%2525252e%2525252e/escape.js?rev=1",
      "plugins/%2Fescape.js?rev=1",
      "plugins/%2525252Fescape.js?rev=1",
      "plugins/%255cescape.js?rev=1",
      "https://example.invalid/plugins/a.js?rev=1",
      "//example.invalid/plugins/a.js?rev=1",
      "/plugins/a.js?rev=1#fragment",
      "/plugins/a.js/",
    ];
    for (const row of invalid) await expect(transport.loadBundle(row)).rejects.toThrow("bundle_url_invalid");
    expect(port.fetchBundle).not.toHaveBeenCalled();
  });

  it("fails closed for invalid paths, Host failures, media types, size and execution errors", async () => {
    const {transport} = fixture();
    await expect(transport.loadBundle("https://example.invalid/plugin.js")).rejects.toThrow("bundle_url_invalid");

    const missing = fixture({fetchBundle: async () => ({status: 404, contentType: null, body: new Uint8Array()})});
    await expect(missing.transport.loadBundle("/plugins/missing.js?rev=1")).rejects.toThrow("bundle_http_404");
    expect(missing.executor.execute).not.toHaveBeenCalled();

    const wrongType = fixture({fetchBundle: async () => ({status: 200, contentType: "text/html", body: encoder.encode("x")})});
    await expect(wrongType.transport.loadBundle("/plugins/a.js?rev=1")).rejects.toThrow("content_type_invalid");

    const tooLarge = fixture({fetchBundle: async () => ({status: 200, contentType: "text/javascript", body: encoder.encode("1234")})});
    const limited = createDshShellTransport(tooLarge.port, tooLarge.executor, {maxBundleBytes: 3});
    await expect(limited.loadBundle("/plugins/a.js?rev=1")).rejects.toThrow("bundle_too_large");

    const execution = fixture();
    vi.mocked(execution.executor.execute).mockRejectedValueOnce(new Error("registration_failed"));
    await expect(execution.transport.loadBundle("/plugins/a.js?rev=1")).rejects.toThrow("registration_failed");
  });

  it("does not claim Host ownership unless production explicitly proves it", () => {
    const {port, executor, transport} = fixture();
    expect(transport.ownsHost).toBeUndefined();
    expect(createDshShellTransport(port, executor, {ownsHost: true}).ownsHost).toBe(true);
  });
});

