/** App-owned provider capacity only; no Session, Turn or inbox semantics. */
export function createNativeProviderAdmission(maximum = 1) {
  if (!Number.isSafeInteger(maximum) || maximum < 1) throw new TypeError('provider_capacity_invalid')
  let active = 0
  let peakActive = 0
  let peakWaiting = 0
  let closed = false
  let admittedAttempts = 0
  const waiters = []
  const sessionActive = new Map()
  const sessionWaiting = new Map()
  const adjust = (map, id, amount) => {
    if (typeof id !== 'string') return
    const count = (map.get(id) ?? 0) + amount
    if (count === 0) map.delete(id)
    else map.set(id, count)
  }

  const take = async (signal) => {
    signal?.throwIfAborted()
    if (closed) throw new Error('provider_admission_closed')
    if (active < maximum) { active += 1; peakActive = Math.max(peakActive, active); return }
    await new Promise((resolve, reject) => {
      const waiter = { resolve, reject, signal, abort: null }
      waiter.abort = () => {
        const index = waiters.indexOf(waiter)
        if (index >= 0) waiters.splice(index, 1)
        reject(signal.reason)
      }
      signal?.addEventListener('abort', waiter.abort, { once: true })
      waiters.push(waiter)
      peakWaiting = Math.max(peakWaiting, waiters.length)
    })
  }

  const release = () => {
    const waiter = waiters.shift()
    if (waiter === undefined) { active -= 1; return }
    waiter.signal?.removeEventListener('abort', waiter.abort)
    // Transfer the existing slot: newly arriving calls cannot overtake FIFO.
    waiter.resolve()
  }

  return Object.freeze({
    async *wrap(options, next) {
      // These counts describe physical provider admission only. They do not
      // infer a Session/Turn outcome while tools, questions or compaction run.
      const queued = active >= maximum
      if (queued) adjust(sessionWaiting, options.sessionId, 1)
      try { await take(options.signal) }
      finally { if (queued) adjust(sessionWaiting, options.sessionId, -1) }
      adjust(sessionActive, options.sessionId, 1)
      try {
        options.signal?.throwIfAborted()
        admittedAttempts += 1
        yield* next()
      } finally { adjust(sessionActive, options.sessionId, -1); release() }
    },
    snapshot: () => Object.freeze({ maximum, active, waiting: waiters.length, peakActive, peakWaiting, admittedAttempts }),
    sessionSnapshot(sessionId) {
      const sessionAttempts = sessionActive.get(sessionId) ?? 0
      const queuedAttempts = sessionWaiting.get(sessionId) ?? 0
      return Object.freeze({ state: sessionAttempts > 0 ? 'active' : queuedAttempts > 0 ? 'queued' : 'idle',
        active: sessionAttempts, waiting: queuedAttempts, queued_by_capacity: queuedAttempts > 0,
        capacity: maximum })
    },
    close() {
      closed = true
      for (const waiter of waiters.splice(0)) {
        waiter.signal?.removeEventListener('abort', waiter.abort)
        waiter.reject(new Error('provider_admission_closed'))
      }
    },
  })
}
