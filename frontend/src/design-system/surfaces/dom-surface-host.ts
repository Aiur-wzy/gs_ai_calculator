import type {SurfaceDescriptor, SurfaceGroup, SurfaceKind} from "@/state/model";
import type {AppStateStore} from "@/state/store";
import {FocusManager} from "@/design-system/focus/focus-manager";
import {SurfaceCoordinator} from "./coordinator";

export type DomSurfaceOpenOptions = Readonly<{
  id: string;
  ownerId: string;
  container: HTMLElement;
  trigger: HTMLElement | null;
  fallback: HTMLElement | null;
  initialFocus?: HTMLElement | null;
  focusOnOpen?: boolean;
  kind: SurfaceKind;
  group: SurfaceGroup;
  modal?: boolean;
  dismissOnEscape?: boolean;
  dismissOnOutside?: boolean;
  dismissOnScroll?: boolean;
  preservePlacement?: boolean;
  onRequestClose?: (reason: string) => boolean;
  onClosed?: (reason: string) => void;
}>;

type RuntimeRecord = Readonly<{
  descriptor: SurfaceDescriptor;
  container: HTMLElement;
  trigger: HTMLElement | null;
  dismissOnScroll: boolean;
  onRequestClose: (reason: string) => boolean;
  onClosed: (reason: string) => void;
}>;

const contains = (root: HTMLElement | null, target: EventTarget | null): boolean =>
  Boolean(root && target && root.contains(target as Node));

/**
 * One DOM lifecycle owner for every Foundation surface.  Serialisable surface
 * identity lives in SurfaceCoordinator; element references and callbacks stay
 * here and never enter the Root Store.
 */
export class DomSurfaceHost {
  private readonly records = new Map<string, RuntimeRecord>();
  private readonly focus: FocusManager;
  private readonly observer: MutationObserver | null;
  private disposed = false;

  constructor(
    private readonly document: Document,
    appRoot: HTMLElement,
    private readonly overlayRoot: HTMLElement,
    private readonly coordinator: SurfaceCoordinator,
    store: AppStateStore,
  ) {
    this.focus = new FocusManager(document, appRoot, coordinator, store, (id, reason) => this.requestClose(id, reason));
    this.focus.attach();
    document.addEventListener("scroll", this.onScroll, true);
    document.addEventListener("visibilitychange", this.onVisibilityChange);
    document.addEventListener("workspacechange", this.onWorkspaceChange);
    document.defaultView?.addEventListener("resize", this.onResize);
    document.defaultView?.addEventListener("blur", this.onBlur);
    document.defaultView?.addEventListener("pagehide", this.onPageHide);
    document.defaultView?.visualViewport?.addEventListener("resize", this.onViewportResize);
    document.defaultView?.visualViewport?.addEventListener("scroll", this.onViewportScroll);
    const Observer = document.defaultView?.MutationObserver;
    this.observer = Observer ? new Observer(() => this.reconcileDisconnected()) : null;
    this.observer?.observe(document.body, {childList: true, subtree: true});
  }

  open(options: DomSurfaceOpenOptions): boolean {
    if (this.disposed) throw new Error("dom_surface_host_disposed");
    if (!options.container || !options.id.trim() || !options.ownerId.trim()) throw new TypeError("dom_surface_identity_required");
    if (this.records.has(options.id)) throw new Error("dom_surface_id_already_open");
    for (const existing of this.coordinator.list().filter(row => row.group === options.group)) {
      if (!this.requestClose(existing.id, "replaced", {restoreFocus: false})) return false;
    }
    const descriptor: SurfaceDescriptor = Object.freeze({
      id: options.id,
      ownerId: options.ownerId,
      kind: options.kind,
      group: options.group,
      modal: options.modal ?? false,
      dismissOnEscape: options.dismissOnEscape ?? true,
      dismissOnOutside: options.dismissOnOutside ?? true,
    });
    const record: RuntimeRecord = Object.freeze({
      descriptor,
      container: options.container,
      trigger: options.trigger,
      dismissOnScroll: options.dismissOnScroll ?? false,
      onRequestClose: options.onRequestClose ?? (() => true),
      onClosed: options.onClosed ?? (() => {}),
    });
    this.records.set(options.id, record);
    try {
      this.coordinator.open(descriptor);
      const alreadyMounted = options.container.parentNode !== null && options.container.isConnected !== false;
      if (!(options.preservePlacement && alreadyMounted) && !contains(this.overlayRoot, options.container)) {
        this.overlayRoot.append(options.container);
      }
      this.focus.bind(options.id, {
        container: options.container,
        trigger: options.trigger,
        fallback: options.fallback,
        initialFocus: options.initialFocus,
        focusOnOpen: options.focusOnOpen,
      });
      return true;
    } catch (error) {
      this.records.delete(options.id);
      this.coordinator.close(options.id);
      throw error;
    }
  }

  requestClose(id: string, reason = "cancel", {restoreFocus = true}: {restoreFocus?: boolean} = {}): boolean {
    const record = this.records.get(id);
    if (!record) return !this.coordinator.list().some(surface => surface.id === id);
    if (record.onRequestClose(reason) === false) return false;
    return this.finalize(id, reason, restoreFocus);
  }

  destroy(id: string, reason = "destroy", {restoreFocus = false}: {restoreFocus?: boolean} = {}): boolean {
    if (!this.records.has(id)) return true;
    return this.finalize(id, reason, restoreFocus);
  }

  getTopmost(): RuntimeRecord | null {
    const descriptors = this.coordinator.list();
    for (let index = descriptors.length - 1; index >= 0; index -= 1) {
      const record = this.records.get(descriptors[index]!.id);
      if (record) return record;
    }
    return null;
  }

  dispose(): void {
    if (this.disposed) return;
    for (const id of [...this.records.keys()]) this.destroy(id, "host-disposed");
    this.disposed = true;
    this.observer?.disconnect();
    this.focus.detach();
    this.document.removeEventListener("scroll", this.onScroll, true);
    this.document.removeEventListener("visibilitychange", this.onVisibilityChange);
    this.document.removeEventListener("workspacechange", this.onWorkspaceChange);
    this.document.defaultView?.removeEventListener("resize", this.onResize);
    this.document.defaultView?.removeEventListener("blur", this.onBlur);
    this.document.defaultView?.removeEventListener("pagehide", this.onPageHide);
    this.document.defaultView?.visualViewport?.removeEventListener("resize", this.onViewportResize);
    this.document.defaultView?.visualViewport?.removeEventListener("scroll", this.onViewportScroll);
  }

  private finalize(id: string, reason: string, restoreFocus: boolean): boolean {
    const record = this.records.get(id);
    if (!record) return true;
    this.records.delete(id);
    this.coordinator.close(id);
    try { record.onClosed(reason); }
    finally { this.focus.unbind(id, {restoreFocus}); }
    return true;
  }

  private dismissAmbient(reason: string): void {
    // Resize, focus loss and workspace visibility are not user decisions to
    // discard modal/drawer drafts. Ephemeral popovers still lose their anchor;
    // owner destruction/disposal explicitly tears down every surface kind.
    for (const descriptor of [...this.coordinator.list()].reverse()) {
      if (descriptor.kind === "ephemeral" && !descriptor.modal) this.requestClose(descriptor.id, reason);
    }
  }

  private reconcileDisconnected(): void {
    for (const [id, record] of this.records) {
      if (record.trigger?.isConnected === false) this.destroy(id, "target-destroyed");
      else if (record.container.isConnected === false) this.destroy(id, "surface-destroyed");
    }
  }

  private readonly onScroll = (event: Event): void => {
    for (const descriptor of [...this.coordinator.list()].reverse()) {
      const record = this.records.get(descriptor.id);
      if (!record || !record.dismissOnScroll) continue;
      if (contains(record.container, event.target) || contains(record.trigger, event.target)) return;
      this.requestClose(descriptor.id, "scroll");
      return;
    }
  };
  private readonly onVisibilityChange = (): void => this.dismissAmbient("visibilitychange");
  private readonly onWorkspaceChange = (): void => this.dismissAmbient("workspacechange");
  private readonly onResize = (): void => this.dismissAmbient("resize");
  private readonly onBlur = (): void => this.dismissAmbient("blur");
  private readonly onPageHide = (): void => this.dismissAmbient("pagehide");
  private readonly onViewportResize = (): void => this.dismissAmbient("viewport-resize");
  private readonly onViewportScroll = (): void => {
    for (const descriptor of [...this.coordinator.list()].reverse()) {
      const record = this.records.get(descriptor.id);
      if (record?.dismissOnScroll) this.requestClose(descriptor.id, "viewport-scroll");
    }
  };
}
