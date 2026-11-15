import type {AppStateStore} from "@/state/store";
import {SurfaceCoordinator} from "@/design-system/surfaces/coordinator";

type Binding = Readonly<{
  container: HTMLElement;
  trigger: HTMLElement | null;
  fallback: HTMLElement | null;
  initialFocus?: HTMLElement | null;
  focusOnOpen?: boolean;
}>;

export type SurfaceDismissReason = "escape" | "outside";
export type SurfaceDismissRequest = (id: string, reason: SurfaceDismissReason) => boolean;

export class FocusManager {
  private readonly bindings = new Map<string, Binding>();
  private unsubscribe: (() => void) | null = null;
  private attached = false;
  private readonly requestDismiss: SurfaceDismissRequest;

  constructor(
    private readonly document: Document,
    private readonly appRoot: HTMLElement,
    private readonly coordinator: SurfaceCoordinator,
    private readonly store: AppStateStore,
    requestDismiss?: SurfaceDismissRequest,
  ) {
    this.requestDismiss = requestDismiss ?? ((id): boolean => {
      const binding = this.bindings.get(id);
      if (!this.coordinator.close(id)) return false;
      if (binding) this.unbind(id);
      return true;
    });
  }

  attach(): void {
    if (this.attached) return;
    this.attached = true;
    this.document.addEventListener("keydown", this.onKeydown, true);
    this.document.addEventListener("pointerdown", this.onPointerdown, true);
    this.unsubscribe = this.store.subscribeSelector(state => state.surfaces, () => this.syncModalState());
    this.syncModalState();
  }

  detach(): void {
    if (!this.attached) return;
    this.attached = false;
    this.document.removeEventListener("keydown", this.onKeydown, true);
    this.document.removeEventListener("pointerdown", this.onPointerdown, true);
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.appRoot.inert = false;
    this.appRoot.removeAttribute("aria-hidden");
  }

  bind(id: string, binding: Binding): () => void {
    this.bindings.set(id, binding);
    if (binding.focusOnOpen !== false) this.initialFocus(binding.container, binding.initialFocus ?? null);
    return () => this.unbind(id);
  }

  unbind(id: string, {restoreFocus = true}: {restoreFocus?: boolean} = {}): boolean {
    const active = this.bindings.get(id);
    if (!active) return false;
    this.bindings.delete(id);
    if (restoreFocus) this.restoreFocus(active);
    return true;
  }

  private initialFocus(container: HTMLElement, preferred: HTMLElement | null): void {
    if (!container.isConnected) return;
    const target = preferred?.isConnected === false ? null : preferred
      ?? container.querySelector?.<HTMLElement>("[autofocus], button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])");
    (target ?? container).focus();
  }

  private restoreFocus(binding: Binding): void {
    const target = binding.trigger && binding.trigger.isConnected !== false ? binding.trigger
      : binding.fallback && binding.fallback.isConnected !== false ? binding.fallback : null;
    target?.focus();
  }

  private syncModalState(): void {
    const modalOpen = this.store.getState().surfaces.some(surface => surface.modal);
    this.appRoot.inert = modalOpen;
    if (modalOpen) this.appRoot.setAttribute("aria-hidden", "true");
    else this.appRoot.removeAttribute("aria-hidden");
  }

  private readonly onKeydown = (event: KeyboardEvent): void => {
    if (event.key !== "Escape" || event.defaultPrevented || event.isComposing) return;
    const surface = this.coordinator.topmostForEscape();
    if (!surface) return;
    event.preventDefault();
    this.requestDismiss(surface.id, "escape");
  };

  private readonly onPointerdown = (event: PointerEvent): void => {
    const surfaces = [...this.coordinator.list()].reverse();
    const target = event.target;
    for (const surface of surfaces) {
      if (!surface.dismissOnOutside) continue;
      const binding = this.bindings.get(surface.id);
      if (!binding || !target) return;
      const node = target as Node;
      if (binding.container.contains(node) || binding.trigger?.contains(node)) return;
      this.requestDismiss(surface.id, "outside");
      return;
    }
  };
}
