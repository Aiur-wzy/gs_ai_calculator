import {startReviewedDshClientComposition, type DshClientCompositionHandle} from "./dsh/client-composition";
import {createHostShellCarrier, type DshShellHostApi, type HostShellCarrier} from "./dsh/host-shell-carrier";
import {withDocumentBinding, type AppEditorBridge, type DocumentBindingApi} from "./dsh/app-editor-bridge";
import {callAppDocumentApi} from "./dsh/app-document-transport";

declare global {
  interface Window {
    GenshinEditorBridge?: AppEditorBridge;
    GenshinDshClient?: Readonly<{ready: Promise<void>; dispose(): Promise<void>}>;
  }
}

if (window.GenshinDshClient) throw new Error("dsh_production_owner_duplicate");
let carrier: HostShellCarrier | undefined;
let composition: DshClientCompositionHandle | undefined;
let disposed = false;
async function dispose(): Promise<void> {
  disposed = true;
  try { await composition?.dispose(); } finally { await carrier?.dispose(); }
}
let started = false;
let resolveReady!: () => void;
let rejectReady!: (reason: unknown) => void;
const ready = new Promise<void>((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
// Errors are visible in the shell; callers may still await the original promise.
void ready.catch(() => {});
window.GenshinDshClient = Object.freeze({ready, dispose: () => dispose()});
const boot = async () => {
  if (started || disposed) return;
  const api = (window as unknown as {pywebview?: {api?: DshShellHostApi & DocumentBindingApi}}).pywebview?.api;
  const editor = window.GenshinEditorBridge;
  if (!api || !editor) return;
  started = true;
  const root = document.getElementById("dsh-app-root")!;
  try {
    carrier = createHostShellCarrier(api);
    const payload = await carrier.boot();
    if (disposed) throw new DOMException("Aborted", "AbortError");
    // This is a desktop client mount, not server-rendered DSH markup. Keeping
    // the App's loading paragraph would make the official renderer attempt
    // hydration against unrelated DOM and force an avoidable React recovery.
    root.replaceChildren();
    const domainApi:DocumentBindingApi={dsh_client_bind_document:request=>callAppDocumentApi(api,"dsh_client_bind_document",[request]),
      ...(api.dsh_client_workspace_state?{dsh_client_workspace_state:(request:Parameters<NonNullable<DocumentBindingApi["dsh_client_workspace_state"]>>[0])=>callAppDocumentApi(api,"dsh_client_workspace_state",[request])}:{})};
    composition = await startReviewedDshClientComposition({container: root, port: withDocumentBinding(carrier, domainApi, editor), boot: payload});
    if (disposed) { await dispose(); throw new DOMException("Aborted", "AbortError"); }
    root.dataset.ready = "true";
    resolveReady();
  } catch (reason) {
    await carrier?.dispose();
    root.replaceChildren();
    const panel = document.createElement("section"); panel.setAttribute("role", "alert");
    panel.style.cssText = "padding:24px;font:14px system-ui;white-space:pre-wrap";
    panel.textContent = `AI 工作台启动失败。请保留以下信息后重新打开应用。\n${reason instanceof Error ? reason.message : String(reason)}`;
    root.append(panel); rejectReady(reason);
  }
};
window.addEventListener("pywebviewready", () => { void boot(); });
document.addEventListener("genshin-editor-ready", () => { void boot(); });
window.addEventListener("beforeunload", () => { void dispose(); }, {once: true});
void boot();
