import type {AppStateStore} from "@/state/store";
import {parseHostEnvelope, type HostEnvelope} from "./schema";

export type EnvelopeAcceptance = Readonly<{
  status: "accepted" | "duplicate";
  envelope: HostEnvelope;
  storeRevision: number;
}>;

export function createHostEnvelopeAdapter(store: AppStateStore) {
  const accept = (input: unknown): EnvelopeAcceptance => {
    const envelope = parseHostEnvelope(input);
    const before = store.getState();
    if (before.lastEnvelopeId === envelope.envelopeId) {
      return Object.freeze({status: "duplicate", envelope, storeRevision: before.revision});
    }
    if (before.hostProjection && envelope.projection.projectionRevision <= before.hostProjection.projectionRevision) {
      throw new Error("projection_revision_not_monotonic");
    }
    const next = store.transact(`host-envelope:${envelope.envelopeId}`, current => ({
      ...current,
      revision: current.revision + 1,
      lastEnvelopeId: envelope.envelopeId,
      hostProjection: envelope.projection,
      issues: envelope.projection.issues,
    }));
    return Object.freeze({status: "accepted", envelope, storeRevision: next.revision});
  };
  return Object.freeze({accept});
}
