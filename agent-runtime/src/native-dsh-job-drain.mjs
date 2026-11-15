/** App shutdown barrier over the upstream job registry, not a job manager. */
export async function drainNativeJobs(ctx, agents) {
  const jobs = ctx.get('jobs')
  if (jobs === undefined) return { requested: 0, terminal: 0 }
  const captured = new Map()
  for (const owner of [undefined, ...agents.map(agent => agent.id)]) {
    for (const job of jobs.list(owner)) captured.set(job.id, { job, owner: job.owner })
  }
  for (const { job, owner } of captured.values()) {
    if (job.status === 'running' || job.status === 'stopping') jobs.kill(job.id, owner, 'App is shutting down')
  }
  for (const { job, owner } of captured.values()) {
    // A bounded observation window is not an execution deadline. A compliant
    // producer's cancellation is owned by DSH; retain communication until its
    // actual terminal state, rather than declaring success after Agent idle.
    while (true) {
      try {
        const observed = await jobs.wait(job.id, 30_000, owner)
        // The upstream wait resolves with a live projection on observation
        // timeout; fulfillment itself is not a terminal job receipt.
        if (!['running', 'stopping'].includes(observed.status)) break
      }
      catch (error) {
        if (error?.code === 'TASK_WAIT_TIMEOUT') continue
        if (error?.code === 'JOB_NOT_FOUND') break // already disposed by its upstream owner
        throw error
      }
    }
  }
  return { requested: captured.size, terminal: captured.size }
}
