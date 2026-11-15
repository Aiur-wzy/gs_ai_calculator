import {describe, expect, it, vi} from "vitest";
import {createAppStateStore} from "@/state/store";
import {createHostEnvelopeAdapter} from "./adapter";
import {HostEnvelopeError} from "./schema";
import {galleryEnvelope} from "@/app/fixture";

const envelope = (id: string, revision: number) => ({
  ...galleryEnvelope,
  envelope_id: id,
  projection: {
    ...galleryEnvelope.projection,
    projection_revision: revision,
    available_commands: galleryEnvelope.projection.available_commands.map(command => ({...command, expected_projection_revision: revision})),
  },
});

describe("Host envelope adapter", () => {
  it("accepts a whole envelope in one store transaction", () => {
    const store = createAppStateStore();
    const listener = vi.fn();
    store.subscribe(listener);
    const result = createHostEnvelopeAdapter(store).accept(envelope("one", 1));
    expect(result.status).toBe("accepted");
    expect(result.storeRevision).toBe(1);
    expect(store.getState().hostProjection?.projectionRevision).toBe(1);
    expect(store.getState().hostProjection?.availableCommands.map(row => row.commandId)).toContain("conversation.send");
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("deduplicates an envelope without another revision", () => {
    const store = createAppStateStore();
    const adapter = createHostEnvelopeAdapter(store);
    adapter.accept(envelope("same", 1));
    expect(adapter.accept(envelope("same", 1)).status).toBe("duplicate");
    expect(store.getState().revision).toBe(1);
  });

  it("rejects stale projections and command revision mismatch", () => {
    const store = createAppStateStore();
    const adapter = createHostEnvelopeAdapter(store);
    adapter.accept(envelope("new", 2));
    expect(() => adapter.accept(envelope("old", 1))).toThrow("projection_revision_not_monotonic");
    const mismatch = envelope("bad", 3);
    mismatch.projection.available_commands[0].expected_projection_revision = 2;
    expect(() => adapter.accept(mismatch)).toThrow(HostEnvelopeError);
    expect(store.getState().revision).toBe(1);
  });

  it("does not infer required actions from issues or candidates", () => {
    const store = createAppStateStore();
    createHostEnvelopeAdapter(store).accept(envelope("truth", 1));
    expect(store.getState().hostProjection?.requiredActions).toEqual([]);
    expect(store.getState().issues).toHaveLength(1);
  });
});
