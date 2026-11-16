import {afterEach, describe, expect, it} from "vitest";
import {createAppStateStore} from "@/state/store";
import type {SurfaceDescriptor} from "@/state/model";
import {SurfaceCoordinator} from "@/design-system/surfaces/coordinator";
import {FocusManager} from "./focus-manager";

const descriptor = (id: string, modal = false): SurfaceDescriptor => ({
  id,
  kind: modal ? "modal" : "ephemeral",
  group: modal ? "modal" : "ephemeral",
  ownerId: "test",
  dismissOnEscape: true,
  dismissOnOutside: true,
  modal,
});

describe("FocusManager", () => {
  afterEach(() => { document.body.replaceChildren(); });

  function fixture() {
    const app = document.createElement("main");
    const trigger = document.createElement("button");
    const surface = document.createElement("section");
    const action = document.createElement("button");
    const outside = document.createElement("button");
    surface.tabIndex = -1;
    surface.append(action);
    app.append(trigger, outside);
    document.body.append(app, surface);
    const store = createAppStateStore();
    const coordinator = new SurfaceCoordinator(store);
    const manager = new FocusManager(document, app, coordinator, store);
    manager.attach();
    return {app, trigger, surface, action, outside, store, coordinator, manager};
  }

  it("makes background inert for a modal and restores focus on Escape", async () => {
    const item = fixture();
    item.trigger.focus();
    item.coordinator.open(descriptor("modal", true));
    item.manager.bind("modal", {container: item.surface, trigger: item.trigger, fallback: null});
    await Promise.resolve();
    expect(item.app.inert).toBe(true);
    expect(document.activeElement).toBe(item.action);
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
    expect(item.coordinator.list()).toEqual([]);
    expect(item.app.inert).toBe(false);
    expect(document.activeElement).toBe(item.trigger);
    item.manager.detach();
  });

  it("dismisses an ephemeral surface on an outside pointer", () => {
    const item = fixture();
    item.coordinator.open(descriptor("menu"));
    item.manager.bind("menu", {container: item.surface, trigger: item.trigger, fallback: null});
    item.outside.dispatchEvent(new MouseEvent("pointerdown", {bubbles: true, cancelable: true}));
    expect(item.coordinator.list()).toEqual([]);
    item.manager.detach();
  });

  it("falls back safely when the trigger was destroyed", () => {
    const item = fixture();
    const fallback = item.outside;
    item.coordinator.open(descriptor("menu"));
    item.manager.bind("menu", {container: item.surface, trigger: item.trigger, fallback});
    item.trigger.remove();
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
    expect(document.activeElement).toBe(fallback);
    item.manager.detach();
  });

  it("does not send pointers through a non-dismissible modal to surfaces beneath it", () => {
    const item = fixture();
    const modal = document.createElement("section");
    const modalButton = document.createElement("button");
    modal.append(modalButton);
    document.body.append(modal);
    item.coordinator.open(descriptor("menu"));
    item.manager.bind("menu", {container: item.surface, trigger: item.trigger, fallback: null});
    item.coordinator.open({...descriptor("modal", true), dismissOnOutside:false});
    item.manager.bind("modal", {container:modal, trigger:null, fallback:null});
    modalButton.dispatchEvent(new MouseEvent("pointerdown", {bubbles:true}));
    document.body.dispatchEvent(new MouseEvent("pointerdown", {bubbles:true}));
    expect(item.coordinator.list().map(surface => surface.id)).toEqual(["menu","modal"]);
    expect(document.activeElement).toBe(modalButton);
    item.manager.detach();
  });
});
