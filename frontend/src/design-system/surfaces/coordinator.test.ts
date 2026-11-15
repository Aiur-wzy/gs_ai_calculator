import {describe, expect, it} from "vitest";
import {createAppStateStore} from "@/state/store";
import type {SurfaceDescriptor} from "@/state/model";
import {SurfaceCoordinator} from "./coordinator";

const surface = (id: string, group: SurfaceDescriptor["group"], overrides: Partial<SurfaceDescriptor> = {}): SurfaceDescriptor => ({
  id,
  group,
  kind: group === "modal" ? "modal" : group.includes("drawer") ? "drawer" : "ephemeral",
  ownerId: "test",
  dismissOnEscape: true,
  dismissOnOutside: true,
  modal: group === "modal",
  ...overrides,
});

describe("SurfaceCoordinator", () => {
  it("enforces one surface per singleton group", () => {
    const store = createAppStateStore();
    const coordinator = new SurfaceCoordinator(store);
    coordinator.open(surface("menu-a", "ephemeral"));
    expect(coordinator.open(surface("menu-b", "ephemeral"))).toEqual(["menu-a"]);
    expect(coordinator.list().map(row => row.id)).toEqual(["menu-b"]);
  });

  it("applies Escape priority: modal child, modal, ephemeral, drawer", () => {
    const coordinator = new SurfaceCoordinator(createAppStateStore());
    coordinator.open(surface("drawer", "right-drawer"));
    coordinator.open(surface("menu", "ephemeral"));
    coordinator.open(surface("modal", "modal"));
    coordinator.open(surface("modal-child", "modal-child"));
    expect(coordinator.topmostForEscape()?.id).toBe("modal-child");
    coordinator.close("modal-child");
    expect(coordinator.topmostForEscape()?.id).toBe("modal");
    coordinator.close("modal");
    expect(coordinator.topmostForEscape()?.id).toBe("menu");
    coordinator.close("menu");
    expect(coordinator.topmostForEscape()?.id).toBe("drawer");
  });

  it("closes all surfaces owned by an unmounted feature", () => {
    const coordinator = new SurfaceCoordinator(createAppStateStore());
    coordinator.open(surface("drawer", "right-drawer"));
    coordinator.open(surface("toast", "toast", {kind: "toast"}));
    expect(coordinator.closeOwner("test")).toEqual(["drawer", "toast"]);
    expect(coordinator.list()).toEqual([]);
  });
});
