import type {SurfaceDescriptor, SurfaceGroup} from "@/state/model";
import type {AppStateStore} from "@/state/store";

const singletonGroups = new Set<SurfaceGroup>(["modal", "modal-child", "left-drawer", "right-drawer", "ephemeral", "tooltip"]);
const escapePriority: Record<SurfaceGroup, number> = {
  "modal-child": 60,
  modal: 50,
  ephemeral: 40,
  "left-drawer": 30,
  "right-drawer": 30,
  tooltip: 20,
  toast: 10,
};

export class SurfaceCoordinator {
  constructor(private readonly store: AppStateStore) {}

  list(): readonly SurfaceDescriptor[] { return this.store.getState().surfaces; }

  open(descriptor: SurfaceDescriptor): readonly string[] {
    if (!descriptor.id.trim() || !descriptor.ownerId.trim()) throw new TypeError("surface_identity_required");
    const existing = this.list();
    if (existing.some(surface => surface.id === descriptor.id)) throw new Error("surface_id_already_open");
    const closed = singletonGroups.has(descriptor.group)
      ? existing.filter(surface => surface.group === descriptor.group).map(surface => surface.id)
      : [];
    this.store.transact(`surface.open:${descriptor.id}`, current => ({
      ...current,
      revision: current.revision + 1,
      surfaces: Object.freeze([...current.surfaces.filter(surface => !closed.includes(surface.id)), Object.freeze({...descriptor})]),
    }));
    return Object.freeze(closed);
  }

  close(id: string): boolean {
    if (!this.list().some(surface => surface.id === id)) return false;
    this.store.transact(`surface.close:${id}`, current => ({
      ...current,
      revision: current.revision + 1,
      surfaces: Object.freeze(current.surfaces.filter(surface => surface.id !== id)),
    }));
    return true;
  }

  closeOwner(ownerId: string): readonly string[] {
    const ids = this.list().filter(surface => surface.ownerId === ownerId).map(surface => surface.id);
    if (!ids.length) return Object.freeze([]);
    this.store.transact(`surface.close-owner:${ownerId}`, current => ({
      ...current,
      revision: current.revision + 1,
      surfaces: Object.freeze(current.surfaces.filter(surface => surface.ownerId !== ownerId)),
    }));
    return Object.freeze(ids);
  }

  topmostForEscape(): SurfaceDescriptor | null {
    const candidates = this.list().filter(surface => surface.dismissOnEscape);
    return candidates.reduce<SurfaceDescriptor | null>((best, current) => {
      if (!best) return current;
      const currentPriority = escapePriority[current.group];
      const bestPriority = escapePriority[best.group];
      if (currentPriority > bestPriority) return current;
      if (currentPriority === bestPriority && candidates.indexOf(current) > candidates.indexOf(best)) return current;
      return best;
    }, null);
  }
}
