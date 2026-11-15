import {createHash} from "node:crypto";

import {bootInjections, type WebBootGraph} from "@deepseek-ai/dsh-client-modules";
import {describe, expect, it, vi} from "vitest";

import {
  DSH_CLIENT_ACCEPTED_MANIFEST,
  DSH_CLIENT_COMPOSITION_CONTRACT,
  auditDshPlatformSingletons,
  createDomScriptBundleExecutor,
  type DshClientBootPayload,
  validateReviewedDshBootPayload,
} from "./client-composition";

function payload(
  ids: readonly string[] = DSH_CLIENT_COMPOSITION_CONTRACT.dynamicModules,
  release = DSH_CLIENT_COMPOSITION_CONTRACT.dshRelease,
): DshClientBootPayload {
  const entries = ids.map((id, index) => {
    const rev = index.toString(16).padStart(12, "0");
    const accepted = DSH_CLIENT_ACCEPTED_MANIFEST.entries.find(entry => entry.id === id);
    return {
      id,
      url: `plugins/??${id}/client.js&rev=${rev}`,
      rev,
      inject: [...(accepted?.inject ?? [])],
      immediately: accepted?.immediately ?? false,
      external: [...(accepted?.external ?? [])],
    };
  });
  const bootstrapIds = ids.includes("@deepseek-ai/dsh-client-modules")
    ? ["@deepseek-ai/dsh-client-modules"]
    : [ids[0]!];
  const applicationIds = ids.filter(id => !bootstrapIds.includes(id));
  const batch = (phase: "bootstrap" | "application", batchIds: readonly string[], rev: string) => ({
    phase,
    url: `plugins/??${batchIds.map(id => `${id}/client.js`).join(",")}&rev=${rev}`,
    rev,
    entries: [...batchIds],
  });
  const batches = [
    batch("bootstrap", bootstrapIds, "aaaaaaaaaaaa"),
    ...(applicationIds.length > 0 ? [batch("application", applicationIds, "bbbbbbbbbbbb")] : []),
  ];
  const graph: WebBootGraph = {
    rev: createHash("sha1").update(JSON.stringify({entries, batches})).digest("hex").slice(0, 12),
    release: {version: release, commit: DSH_CLIENT_COMPOSITION_CONTRACT.dshCommit},
    entries,
    batches,
  };
  return {injections: bootInjections(graph)};
}

describe("reviewed DSH client composition", () => {
  it("pins one official platform singleton table", () => {
    expect(auditDshPlatformSingletons()).toEqual({
      modules: DSH_CLIENT_COMPOSITION_CONTRACT.platformModules,
      staticModuleCount: DSH_CLIENT_COMPOSITION_CONTRACT.platformModules.length,
    });
  });

  it("accepts exactly the reviewed rc.5 dynamic graph", async () => {
    const graph = await validateReviewedDshBootPayload(payload());
    expect(graph.entries).toHaveLength(DSH_CLIENT_COMPOSITION_CONTRACT.dynamicModules.length);
  });

  it("rejects missing, extra, duplicate and wrong-release graphs", async () => {
    const rows = DSH_CLIENT_COMPOSITION_CONTRACT.dynamicModules;
    await expect(validateReviewedDshBootPayload(payload(rows.slice(1)))).rejects.toThrow("allowlist_mismatch");
    await expect(validateReviewedDshBootPayload(payload([...rows, "@deepseek-ai/dsh-client-ui-terminal"])))
      .rejects.toThrow("allowlist_mismatch");
    await expect(validateReviewedDshBootPayload(payload([...rows, rows[0]!]))).rejects.toThrow("entry_duplicate");
    await expect(validateReviewedDshBootPayload(payload(rows, "0.2.1-rc.4"))).rejects.toThrow("release_mismatch");
  });

  it("rejects unreviewed index-injection capabilities before script execution", async () => {
    const invalid = payload();
    const injections = [
      ...invalid.injections,
      {kind: "html", placement: "body", html: "<iframe src='https://example.invalid'></iframe>"},
    ];
    await expect(validateReviewedDshBootPayload({injections} as unknown as DshClientBootPayload))
      .rejects.toThrow("index_injection_shape_invalid");
  });

  it("binds the only executable rows to the pinned queue and reviewed graph batches", async () => {
    const queueChanged = payload();
    const queueRows = [...queueChanged.injections];
    queueRows[0] = {...queueRows[0]!, text: `${(queueRows[0] as {text: string}).text}\nvoid 0`};
    await expect(validateReviewedDshBootPayload({injections: queueRows} as DshClientBootPayload))
      .rejects.toThrow("inline_script_identity_invalid");

    const sourceChanged = payload();
    const sourceRows = [...sourceChanged.injections];
    const sourceIndex = sourceRows.findIndex(row => row.kind === "script-src");
    sourceRows[sourceIndex] = {...sourceRows[sourceIndex]!, src: "plugins/??other/client.js&rev=aaaaaaaaaaaa"};
    await expect(validateReviewedDshBootPayload({injections: sourceRows} as DshClientBootPayload))
      .rejects.toThrow("index_injection_binding_invalid");
  });

  it("rejects allowed packages whose dependency semantics differ from the accepted manifest", async () => {
    const changed = payload();
    const graphRow = changed.injections.find(row => row.kind === "global" && row.name === "__DSH_BOOT__");
    if (graphRow?.kind !== "global") throw new Error("test_graph_missing");
    const graph = graphRow.value as WebBootGraph;
    const target = graph.entries.find(entry => entry.id === "@deepseek-ai/dsh-client-ui-chat")!;
    target.inject = [];
    graph.rev = createHash("sha1")
      .update(JSON.stringify({entries: graph.entries, batches: graph.batches}))
      .digest("hex")
      .slice(0, 12);
    await expect(validateReviewedDshBootPayload(changed))
      .rejects.toThrow("entry_semantics_mismatch:@deepseek-ai/dsh-client-ui-chat");
  });

  it("retains the full reviewed DSH abilities and independent App deployment policies", () => {
    const surface = DSH_CLIENT_COMPOSITION_CONTRACT.dynamicModules.join("\n");
    for (const token of DSH_CLIENT_COMPOSITION_CONTRACT.forbiddenPackageTokens) {
      expect(surface, token).not.toContain(token);
    }
    expect(DSH_CLIENT_COMPOSITION_CONTRACT.permissions).toMatchObject({
      localHttpServer: false,
      filesystem: true,
      shell: true,
      terminal: true,
      mcp: true,
      pluginMarket: false,
      subagent: true,
    });
  });

  it("executes reviewed bytes through a transient DOM script rather than eval or Function", async () => {
    const append = vi.spyOn(document.head, "append");
    const executor = createDomScriptBundleExecutor(document);
    await executor.execute({
      url: "/plugins/@deepseek-ai/dsh-client-modules/client.js?rev=1",
      source: "window.__dshCompositionProbe = true;",
    });
    expect(append).toHaveBeenCalledTimes(1);
    const script = append.mock.calls[0]?.[0] as HTMLScriptElement;
    expect(script.dataset.dshBundle).toContain("dsh-client-modules");
    expect(script.textContent).toContain("__dshCompositionProbe");
    expect(script.isConnected).toBe(false);
  });
});
