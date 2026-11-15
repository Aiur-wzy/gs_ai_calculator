import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdir, mkdtemp, readFile, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { preserveMigrationBackup } from './native-dsh-full-profile.mjs'
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'

test('a restart reuses a completed migration backup without deleting or overwriting it', async () => {
  const root = await mkdtemp(join(tmpdir(), 'dsh-migration-backup-'))
  try {
    const file = join(root, 'pre-migration-cordis.patch.yml')
    const previous = '# user-owned YAML remains verbatim\n- id: agent-loop\n  config: { maxSteps: 12 }\n'
    await preserveMigrationBackup(file, previous)
    await preserveMigrationBackup(file, previous) // crash after backup, before patch/marker
    assert.equal(await readFile(file, 'utf8'), previous)
    await assert.rejects(preserveMigrationBackup(file, previous + '# unrelated edit'),
      /native_dsh_settings_migration_backup_conflict/u)
    assert.equal(await readFile(file, 'utf8'), previous)
  } finally {
    assert.ok(root.startsWith(join(tmpdir(), 'dsh-migration-backup-')))
    await rm(root, { recursive: true, force: true })
  }
})

test('concurrent backup writers share the official lock and retain one exact original', async () => {
  const root = await mkdtemp(join(tmpdir(), 'dsh-migration-backup-'))
  try {
    const file = join(root, 'backup.yml')
    await writeFile(file, 'original')
    await Promise.all([preserveMigrationBackup(file, 'original'), preserveMigrationBackup(file, 'original')])
    assert.equal(await readFile(file, 'utf8'), 'original')
  } finally {
    assert.ok(root.startsWith(join(tmpdir(), 'dsh-migration-backup-')))
    await rm(root, { recursive: true, force: true })
  }
})

test('production profile boot resumes after backup publication and cold-restores its migration', async () => {
  const root = await mkdtemp(join(tmpdir(), 'dsh-migration-backup-'))
  let host
  const originalFetch = globalThis.fetch
  globalThis.fetch = () => { throw new Error('migration test must not call a provider') }
  try {
    const profile = join(root, 'runtime-profile')
    await mkdir(profile)
    const source = '# user patch before interrupted migration\n[]\n'
    await writeFile(join(profile, 'cordis.patch.yml'), source)
    await writeFile(join(profile, 'pre-migration-cordis.patch.yml'), source)
    host = await createNativeDshSessionHost({ profileRoot: root, dshSettings: { agentLoop: {} } })
    assert.equal(await readFile(join(profile, 'pre-migration-cordis.patch.yml'), 'utf8'), source)
    const migratedPatch = await readFile(join(profile, 'cordis.patch.yml'), 'utf8')
    assert.equal(JSON.parse(migratedPatch).filter(row => row.id === 'agent-loop').length, 1)
    assert.equal(JSON.parse(await readFile(join(profile, 'legacy-settings-migration.json'), 'utf8')).owner, 'dsh-config-editor')
    await host.dispose()
    host = await createNativeDshSessionHost({ profileRoot: root, dshSettings: { agentLoop: {} } })
    assert.equal(await readFile(join(profile, 'cordis.patch.yml'), 'utf8'), migratedPatch)
    host.kernel.fullProfile.assertHealthy()
  } finally {
    await host?.dispose()
    globalThis.fetch = originalFetch
    assert.ok(root.startsWith(join(tmpdir(), 'dsh-migration-backup-')))
    await rm(root, { recursive: true, force: true })
  }
})
