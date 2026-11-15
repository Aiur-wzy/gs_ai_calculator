import {afterEach, describe, expect, it} from "vitest";
import {createAppStateStore} from "@/state/store";
import {SurfaceCoordinator} from "./coordinator";
import {DomSurfaceHost} from "./dom-surface-host";

describe("DomSurfaceHost", () => {
  afterEach(() => document.body.replaceChildren());

  function fixture() {
    const app = document.createElement("main");
    const overlay = document.createElement("div");
    const trigger = document.createElement("button");
    const outside = document.createElement("button");
    app.append(trigger, outside);
    document.body.append(app, overlay);
    const store = createAppStateStore();
    const coordinator = new SurfaceCoordinator(store);
    const host = new DomSurfaceHost(document, app, overlay, coordinator, store);
    return {app, overlay, trigger, outside, store, coordinator, host};
  }

  it("portals one ephemeral surface and delegates outside dismissal", () => {
    const item = fixture();
    const surface = document.createElement("section");
    const action = document.createElement("button");
    surface.append(action);
    let closed = "";
    expect(item.host.open({
      id: "one", ownerId: "test", container: surface, trigger: item.trigger, fallback: null,
      initialFocus: action, kind: "ephemeral", group: "ephemeral",
      onClosed: reason => { closed = reason; surface.remove(); },
    })).toBe(true);
    expect(surface.parentElement).toBe(item.overlay);
    expect(document.activeElement).toBe(action);
    item.outside.dispatchEvent(new MouseEvent("pointerdown", {bubbles: true, cancelable: true}));
    expect(closed).toBe("outside");
    expect(item.coordinator.list()).toEqual([]);
    expect(document.activeElement).toBe(item.trigger);
    item.host.dispose();
  });

  it("does not replace a surface that refuses to close", () => {
    const item = fixture();
    const first = document.createElement("section");
    const second = document.createElement("section");
    expect(item.host.open({
      id: "first", ownerId: "test", container: first, trigger: item.trigger, fallback: null,
      kind: "ephemeral", group: "ephemeral", onRequestClose: () => false,
    })).toBe(true);
    expect(item.host.open({
      id: "second", ownerId: "test", container: second, trigger: null, fallback: null,
      kind: "ephemeral", group: "ephemeral",
    })).toBe(false);
    expect(item.coordinator.list().map(row => row.id)).toEqual(["first"]);
    item.host.destroy("first");
    item.host.dispose();
  });

  it("owns ambient dismissal listeners once for multiple sequential surfaces", () => {
    const item = fixture();
    for (const id of ["first", "second"]) {
      const surface = document.createElement("section");
      expect(item.host.open({
        id, ownerId: "test", container: surface, trigger: item.trigger, fallback: null,
        kind: "ephemeral", group: "ephemeral", dismissOnScroll: true,
        onClosed: () => surface.remove(),
      })).toBe(true);
      window.dispatchEvent(new Event("resize"));
      expect(item.coordinator.list()).toEqual([]);
    }
    item.host.dispose();
  });

  it("keeps a modal child mounted inside its parent while sharing the host lifecycle", () => {
    const item = fixture();
    const modal = document.createElement("dialog");
    const child = document.createElement("section");
    modal.append(child);
    item.overlay.append(modal);
    expect(item.host.open({
      id: "child", ownerId: "test", container: child, trigger: null, fallback: null,
      kind: "drawer", group: "modal-child", preservePlacement: true,
    })).toBe(true);
    expect(child.parentElement).toBe(modal);
    expect(item.coordinator.list().map(row => row.group)).toEqual(["modal-child"]);
    item.host.destroy("child");
    item.host.dispose();
  });
});
