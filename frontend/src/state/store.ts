import {initialAppState, type AppState} from "./model";

type Listener = () => void;
type Selector<T> = (state: AppState) => T;
type Equality<T> = (left: T, right: T) => boolean;
type Transaction = (state: AppState) => AppState;

const isPlainObject = (value: unknown): value is Record<string, unknown> => {
  if (value === null || typeof value !== "object") return false;
  const prototype = Object.getPrototypeOf(value);
  if (prototype === null) return true;
  const constructor = prototype.constructor;
  return typeof constructor === "function"
    && Function.prototype.toString.call(constructor) === Function.prototype.toString.call(Object);
};

export function assertSerializable(value: unknown, path = "state", seen = new WeakSet<object>()): void {
  if (value === null || ["string", "boolean"].includes(typeof value)) return;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new TypeError(`non_serializable_number:${path}`);
    return;
  }
  if (["undefined", "function", "symbol", "bigint"].includes(typeof value)) {
    throw new TypeError(`non_serializable_value:${path}`);
  }
  if (typeof value !== "object") return;
  if ((typeof Node !== "undefined" && value instanceof Node)
    || value instanceof Promise
    || (typeof AbortController !== "undefined" && value instanceof AbortController)) {
    throw new TypeError(`runtime_value_in_store:${path}`);
  }
  if (seen.has(value)) throw new TypeError(`cyclic_store_value:${path}`);
  seen.add(value);
  if (Array.isArray(value)) {
    value.forEach((child, index) => assertSerializable(child, `${path}[${index}]`, seen));
  } else {
    if (!isPlainObject(value)) throw new TypeError(`non_plain_store_value:${path}`);
    for (const [key, child] of Object.entries(value)) assertSerializable(child, `${path}.${key}`, seen);
  }
  seen.delete(value);
}

function deepFreeze<T>(value: T, seen = new WeakSet<object>()): T {
  if (value === null || typeof value !== "object" || seen.has(value as object)) return value;
  seen.add(value as object);
  Object.values(value as Record<string, unknown>).forEach(child => deepFreeze(child, seen));
  return Object.freeze(value);
}

export type AppStateStore = Readonly<{
  getState(): AppState;
  transact(label: string, transaction: Transaction): AppState;
  subscribe(listener: Listener): () => void;
  subscribeSelector<T>(selector: Selector<T>, listener: (value: T, previous: T) => void, equality?: Equality<T>): () => void;
}>;

export function createAppStateStore(seed: AppState = initialAppState()): AppStateStore {
  assertSerializable(seed);
  let state = deepFreeze(seed);
  const listeners = new Set<Listener>();
  let transacting = false;

  const transact = (label: string, transaction: Transaction): AppState => {
    if (!label.trim()) throw new TypeError("transaction_label_required");
    if (transacting) throw new Error("nested_store_transaction");
    transacting = true;
    try {
      const proposed = transaction(state);
      if (proposed === state) return state;
      assertSerializable(proposed);
      if (proposed.revision !== state.revision + 1) throw new Error("store_revision_must_advance_once");
      state = deepFreeze(proposed);
    } finally {
      transacting = false;
    }
    listeners.forEach(listener => listener());
    return state;
  };

  const subscribe = (listener: Listener): (() => void) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  };

  const subscribeSelector = <T>(selector: Selector<T>, listener: (value: T, previous: T) => void, equality: Equality<T> = Object.is): (() => void) => {
    let selected = selector(state);
    return subscribe(() => {
      const next = selector(state);
      if (equality(selected, next)) return;
      const previous = selected;
      selected = next;
      listener(next, previous);
    });
  };

  return Object.freeze({getState: () => state, transact, subscribe, subscribeSelector});
}
