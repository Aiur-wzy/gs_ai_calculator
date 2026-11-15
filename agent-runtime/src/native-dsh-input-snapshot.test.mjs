import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { Context } from '@deepseek-ai/cordis'
import LocalFileSystem from '@deepseek-ai/dsh-fs-local'
import { captureNativeInputSnapshot } from './native-dsh-input-snapshot.mjs'

test('imports exact bytes through public DSH filesystem with the owning working directory', async () => {
  const root = await mkdtemp(join(tmpdir(), 'dsh-input-snapshot-'))
  const ctx = new Context()
  try {
    await ctx.plugin(LocalFileSystem, { cwd: root })
    const raw = Buffer.from('\uFEFF{"large_integer":9007199254740993,"text":"中文"}\r\n', 'utf8')
    await writeFile(join(root, 'input.json'), raw)
    let selected
    const world = { fs: ctx.fs, workingDirectory: { async ensure(agent) { selected = agent; return root } }, emit() {} }
    const agent = { id: 'real-fs-engineering-agent' }
    const snapshot = await captureNativeInputSnapshot(world, 'gs_patch_candidate', {
      mode: 'import', file_path: 'input.json', expected_file_sha: createHash('sha256').update(raw).digest('hex'),
    }, { agent, signal: new AbortController().signal })
    assert.equal(selected, agent)
    assert.deepEqual(Buffer.from(snapshot.content_base64, 'base64'), raw)
    assert.equal(snapshot.bytes, raw.length)
    assert.equal(snapshot.requested_path, 'input.json')
  } finally {
    await ctx.fiber.dispose()
    assert.ok(root.startsWith(join(tmpdir(), 'dsh-input-snapshot-')))
    await rm(root, { recursive: true, force: true })
  }
})

test('filesystem denial has no Python or node:fs fallback', async () => {
  let reads = 0
  const error = Object.assign(new Error('backend denied'), { code: 'FS_SANDBOX_DENIED' })
  const ctx = { fs: { async resolve() { throw error }, async readBytes() { reads++; return new Uint8Array() } },
    workingDirectory: { async ensure() { return 'workspace' } } }
  await assert.rejects(captureNativeInputSnapshot(ctx, 'gs_patch_whiteboard',
    { mode: 'import', file_path: 'forbidden.json' }, {}), value => value === error)
  assert.equal(reads, 0)
})

test('file revision and expected SHA are separate guards before domain execution', async () => {
  let reads = 0, stats = 0
  const raw = Buffer.from('{}')
  const ctx = { fs: {
    async resolve() { return { targetKey: 'opaque-key', displayPath: 'actual.json' } },
    async stat() { return { type: 'file', version: ++stats === 1 ? 'v1' : 'v2', size: raw.length } },
    async readBytes() { reads++; return raw },
  }, workingDirectory: { async ensure() { return 'workspace' } }, emit() {} }
  await assert.rejects(captureNativeInputSnapshot(ctx, 'gs_patch_candidate',
    { mode: 'import', file_path: 'input.json' }, {}), { code: 'file_conflict' })
  ctx.fs.stat = async () => ({ type: 'file', version: 'stable', size: raw.length })
  await assert.rejects(captureNativeInputSnapshot(ctx, 'gs_patch_candidate',
    { mode: 'import', file_path: 'input.json', expected_file_sha: '0'.repeat(64) }, {}), { code: 'file_conflict' })
  assert.equal(reads, 2)
})

test('plain patch/replace neither opens files nor creates a permission receipt', async () => {
  assert.equal(await captureNativeInputSnapshot({}, 'gs_patch_candidate', { mode: 'replace', input: {} }, {}), undefined)
})
