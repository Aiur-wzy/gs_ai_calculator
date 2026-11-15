import {Script} from "node:vm";

import {Context} from "@deepseek-ai/cordis";
import Loader from "@deepseek-ai/cordis-plugin-loader";
import {bootInjections} from "@deepseek-ai/dsh-client-modules";
import {AppWebEntry} from "@deepseek-ai/dsh-client-web";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";

import {
  DSH_CLIENT_COMPOSITION_CONTRACT,
  startReviewedDshClientComposition,
  type DshClientCompositionHandle,
} from "./client-composition";
import type {ShellCarrierPort} from "./shell-carrier";

class NoopResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

let client: DshClientCompositionHandle | undefined;
let host: Context | undefined;
let injections: ReturnType<typeof bootInjections>;

const BOOT_NAMES = ["__DSH_TRANSPORT__", "__DSH_BOOT__", "__ModuleLoader__"] as const;

function vmExecutor() {
  const jsdom = (globalThis as typeof globalThis & {
    jsdom: {getInternalVMContext(): ReturnType<typeof import("node:vm")["createContext"]>};
  }).jsdom;
  return {
    execute({url, source}: Readonly<{url: string; source: string}>): void {
      const context = jsdom.getInternalVMContext();
      Reflect.set(context, "TextEncoder", TextEncoder);
      Reflect.set(context, "TextDecoder", TextDecoder);
      new Script(source, {filename: url}).runInContext(context);
      const loader = new Script("window.__ModuleLoader__").runInContext(context);
      if (loader !== undefined) Reflect.set(globalThis, "__ModuleLoader__", loader);
    },
  };
}

function shellPort(tamper: "none" | "bytes" | "trailer" = "none"): ShellCarrierPort {
  let tampered = false;
  return {
    async unary() {
      return {
        ok: false,
        error: {code: "spike_host_unavailable", message: "No production Remote host in Phase 1 spike.", details: {}},
      };
    },
    stream() {
      return (async function* () { return; })();
    },
    async fetchBundle(url) {
      const response = await host!.clientModules.fetchBundle(
        new Request(new URL(url, "https://dsh-shell.invalid/")),
      );
      let body = new Uint8Array(await response.arrayBuffer());
      if (tamper === "bytes" && !tampered && body.byteLength > 0) {
        tampered = true;
        body[0] = body[0]! ^ 1;
      } else if (tamper === "trailer" && !tampered) {
        tampered = true;
        const source = new TextDecoder().decode(body);
        body = new TextEncoder().encode(source.replace(
          /\/\/# sourceMappingURL=[^\n]+\n$/,
          "//# sourceMappingURL=https://unreviewed.invalid/map\n",
        ));
      }
      return {
        status: response.status,
        contentType: response.headers.get("content-type"),
        body,
      };
    },
  };
}

function createContainer(): HTMLElement {
  const container = document.createElement("main");
  document.body.append(container);
  return container;
}

function expectBootGlobalsReleased(): void {
  for (const name of BOOT_NAMES) expect(Object.prototype.hasOwnProperty.call(globalThis, name), name).toBe(false);
  expect(Reflect.get(globalThis, Symbol.for("genshin.dsh-client-composition.owner"))).toBeUndefined();
}

beforeEach(async () => {
  Object.defineProperty(globalThis, "ResizeObserver", {value: NoopResizeObserver, configurable: true});
  Object.defineProperty(globalThis, "matchMedia", {
    configurable: true,
    value: () => ({
      matches: false,
      media: "",
      onchange: null,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() { return true; },
    }),
  });
  host = new Context();
  await host.plugin(Loader, {baseUrl: import.meta.url});
  for (const name of DSH_CLIENT_COMPOSITION_CONTRACT.dynamicModules.filter(
    name => name !== "@deepseek-ai/dsh-client-modules",
  )) {
    await host.loader.create({name});
  }
  await host.loader.create({name: "@deepseek-ai/dsh-client-modules"});
  await host.loader.await();
  if (host.clientModules === undefined) throw new Error("test_client_module_registry_missing");
  injections = bootInjections(host.clientModules.graph());
});

afterEach(async () => {
  await client?.dispose();
  client = undefined;
  await host?.fiber.dispose();
  host = undefined;
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

describe("real rc.5 DSH client composition", () => {
  it("boots the reviewed graph through ClientModuleRegistry and GenshinShellCarrier", async () => {
    // Vitest exposes JSDOM's objects through a Node-global proxy, where a
    // dynamically appended classic script is not executed.  Use JSDOM's own
    // VM context for those exact reviewed bytes; this is test infrastructure,
    // while production remains the DOM-script executor exercised separately.
    const container = createContainer();
    client = await startReviewedDshClientComposition({
      container,
      port: shellPort(),
      boot: {injections},
      executor: vmExecutor(),
    });

    expect(client.contract).toBe(DSH_CLIENT_COMPOSITION_CONTRACT.contract);
    expect(container.childElementCount).toBeGreaterThan(0);
    expect(Reflect.get(globalThis, "__DSH_TRANSPORT__")).toBeDefined();
  }, 30_000);

  it("rejects Host bundle bytes that do not match the accepted digest", async () => {
    await expect(startReviewedDshClientComposition({
      container: createContainer(),
      port: shellPort("bytes"),
      boot: {injections},
      executor: vmExecutor(),
    })).rejects.toThrow("bundle_digest_mismatch:@deepseek-ai/dsh-client-modules");
    expectBootGlobalsReleased();
  });

  it("does not normalize or execute an unreviewed source-map trailer", async () => {
    await expect(startReviewedDshClientComposition({
      container: createContainer(),
      port: shellPort("trailer"),
      boot: {injections},
      executor: vmExecutor(),
    })).rejects.toThrow("bundle_trailer_mismatch:@deepseek-ai/dsh-client-modules");
    expectBootGlobalsReleased();
  });

  it("preserves boot and observer failures while disposing and restoring globals", async () => {
    const bootFailure = new Error("fixture_boot_failure");
    const observerFailure = new Error("fixture_observer_failure");
    const dispose = vi.spyOn(AppWebEntry.prototype, "dispose").mockResolvedValue();
    vi.spyOn(AppWebEntry.prototype, "run").mockImplementation(async onFailure => {
      onFailure?.(bootFailure);
    });
    let observed: unknown;
    try {
      await startReviewedDshClientComposition({
        container: createContainer(),
        port: shellPort(),
        boot: {injections},
        executor: vmExecutor(),
        onFailure() { throw observerFailure; },
      });
    } catch (error) {
      observed = error;
    }
    expect(observed).toBeInstanceOf(AggregateError);
    expect((observed as AggregateError).errors).toEqual([bootFailure, observerFailure]);
    expect(dispose).toHaveBeenCalledOnce();
    expectBootGlobalsReleased();
  });

  it("restores globals even when explicit disposal fails", async () => {
    vi.spyOn(AppWebEntry.prototype, "run").mockResolvedValue();
    const disposeFailure = new Error("fixture_dispose_failure");
    vi.spyOn(AppWebEntry.prototype, "dispose").mockRejectedValue(disposeFailure);
    client = await startReviewedDshClientComposition({
      container: createContainer(),
      port: shellPort(),
      boot: {injections},
      executor: vmExecutor(),
    });
    const handle = client;
    client = undefined;
    await expect(handle.dispose()).rejects.toBe(disposeFailure);
    expectBootGlobalsReleased();
  });

  it("does not hide a boot failure when startup disposal also fails", async () => {
    const bootFailure = new Error("fixture_boot_failure");
    const disposeFailure = new Error("fixture_dispose_failure");
    vi.spyOn(AppWebEntry.prototype, "run").mockImplementation(async onFailure => {
      onFailure?.(bootFailure);
    });
    vi.spyOn(AppWebEntry.prototype, "dispose").mockRejectedValue(disposeFailure);
    let observed: unknown;
    try {
      await startReviewedDshClientComposition({
        container: createContainer(),
        port: shellPort(),
        boot: {injections},
        executor: vmExecutor(),
      });
    } catch (error) {
      observed = error;
    }
    expect(observed).toBeInstanceOf(AggregateError);
    expect((observed as AggregateError).errors).toEqual([bootFailure, disposeFailure]);
    expectBootGlobalsReleased();
  });
});
