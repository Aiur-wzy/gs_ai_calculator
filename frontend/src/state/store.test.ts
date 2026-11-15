import {describe, expect, it, vi} from "vitest";
import {initialAppState} from "./model";
import {assertSerializable, createAppStateStore} from "./store";

describe("AppStateStore", () => {
  it("commits one revision and notifies once per transaction", () => {
    const store = createAppStateStore();
    const listener = vi.fn();
    store.subscribe(listener);
    store.transact("search", current => ({
      ...current,
      revision: current.revision + 1,
      ui: Object.freeze({...current.ui, search: "申鹤"}),
    }));
    expect(store.getState().revision).toBe(1);
    expect(store.getState().ui.search).toBe("申鹤");
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("rolls back on validation or reducer failure", () => {
    const store = createAppStateStore();
    expect(() => store.transact("invalid", current => ({...current, ui: {...current.ui, search: "bad"}}))).toThrow("store_revision_must_advance_once");
    expect(store.getState()).toEqual(initialAppState());
    expect(() => store.transact("throw", () => { throw new Error("boom"); })).toThrow("boom");
    expect(store.getState().revision).toBe(0);
  });

  it("rejects runtime objects, callbacks and cycles", () => {
    expect(() => assertSerializable({callback: () => undefined})).toThrow("non_serializable_value");
    expect(() => assertSerializable({node: document.createElement("div")})).toThrow("runtime_value_in_store");
    const cycle: Record<string, unknown> = {};
    cycle.self = cycle;
    expect(() => assertSerializable(cycle)).toThrow("cyclic_store_value");
  });
});
