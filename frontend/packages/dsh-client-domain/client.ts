import type {Context} from "@deepseek-ai/cordis";
import {createAppDomainClientPlugin} from "../../src/dsh/app-domain-plugin";
import type {AppCandidateGuiApi, AppDomainBindings} from "../../src/dsh/app-domain-candidate";
import type {} from "@deepseek-ai/dsh-api-session-controller/client";
import {createAppDomainBridge, createProductionAppDomainApi, type AppDomainHostApi} from "../../src/dsh/app-domain-bridge";
import {createAppEditorPlugin} from "../../src/dsh/app-editor-plugin";
import type {AppEditorBridge} from "../../src/dsh/app-editor-bridge";
import {createAppCredentialSettingsPlugin,type AppCredentialHostApi} from "../../src/dsh/app-credential-settings";
import {createAppRuntimeSettingsPlugin,type AppRuntimeSettingsApi} from "../../src/dsh/app-runtime-settings";
import {createWindowPresentationPlugin,detachedWindow} from "../../src/dsh/app-window-presentation";

declare module "@deepseek-ai/cordis" {
  interface Context {
    /** Exact App Host projection, supplied by the desktop domain bridge. */
    genshinAppDomain: Readonly<{bindings: AppDomainBindings; api: AppCandidateGuiApi}>;
  }
}

export const inject = ["slots", "sidebarRightTabs", "sessions"];

export function apply(ctx: Context) {
  const editor = (window as unknown as {GenshinEditorBridge?: AppEditorBridge}).GenshinEditorBridge;
  if (editor && !detachedWindow()) void ctx.plugin(createAppEditorPlugin(editor));
  void ctx.plugin(createWindowPresentationPlugin());
  let installed = false;
  let disposed = false;
  const install = () => {
    const host = (window as unknown as {pywebview?: {api?: AppDomainHostApi}}).pywebview?.api;
    if (installed || disposed || typeof host?.dsh_client_domain_binding !== "function"
      || typeof host.dsh_client_candidate !== "function" || typeof host.dsh_client_candidate_read !== "function") return;
    const api = createProductionAppDomainApi(host);
    installed = true;
    const bridge = createAppDomainBridge(api, ctx.sessions);
    ctx.provide("genshinAppDomain", bridge);
    ctx.effect(() => () => bridge.dispose());
    void ctx.plugin(createAppDomainClientPlugin(bridge));
    const credentials=host as AppDomainHostApi & AppCredentialHostApi;
    if(typeof credentials.dsh_client_credential_status==="function"&&typeof credentials.dsh_client_credential_save==="function"&&typeof credentials.dsh_client_credential_delete==="function")void ctx.plugin(createAppCredentialSettingsPlugin(credentials));
    const settings=host as AppDomainHostApi & AppRuntimeSettingsApi;
    if(["agent_budget_settings","agent_update_budget_settings","agent_reset_budget_settings","agent_dsh_settings","agent_update_dsh_settings","agent_reset_dsh_settings"].every(key=>typeof Reflect.get(settings,key)==="function"))void ctx.plugin(createAppRuntimeSettingsPlugin(settings));
  };
  ctx.effect(() => {
    window.addEventListener("pywebviewready", install);
    install();
    return () => { disposed = true; window.removeEventListener("pywebviewready", install); };
  });
}
