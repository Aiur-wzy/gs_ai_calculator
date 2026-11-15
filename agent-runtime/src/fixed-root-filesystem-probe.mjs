/** Offline adversarial qualification for the App-private fixed-root filesystem. */
import { Server as NetServer } from 'node:net'
import {
  appendFile,
  link,
  mkdir,
  mkdtemp,
  rm,
  symlink,
  writeFile,
} from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { Context } from '@deepseek-ai/cordis'
import { FsError } from '@deepseek-ai/dsh-fs'

import {
  FIXED_ROOT_FILESYSTEM_CONTRACT,
  FIXED_ROOT_FILESYSTEM_VERSION,
  FIXED_ROOT_FORBIDDEN_CAPABILITIES,
  FIXED_ROOT_POLICY,
  FIXED_ROOT_REJECTED_METHODS,
  FIXED_ROOT_SUPPORTED_METHODS,
  FIXED_ROOT_VIRTUAL_ROOT,
  assertFixedRootFilesystemContract,
} from './fixed-root-filesystem-contract.mjs'
import { FixedRootFileSystem } from './fixed-root-filesystem.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'

const FILE_LEAF = `${'a'.repeat(64)}.json`
const HARDLINK_LEAF = `${'b'.repeat(64)}.bin`
const LINK_LEAF = `${'c'.repeat(64)}.bin`
const GROWTH_LEAF = `${'d'.repeat(64)}.bin`
const CANCEL_LEAF = `${'e'.repeat(64)}.bin`
const DIRECTORY_LEAF = 'f'.repeat(64)
const NESTED_LEAF = '0'.repeat(64)
const MISSING_LEAF = '2'.repeat(64)

function assert(condition, code) {
  if (!condition) throw new Error(code)
}

async function expectFsError(operation, code) {
  let caught
  try {
    await operation()
  } catch (error) {
    caught = error
  }
  assert(caught instanceof FsError, `fixed_root_probe_error_type:${code}`)
  assert(caught.code === code, `fixed_root_probe_error_code:${code}:${caught?.code}`)
  return caught
}

function services(ctx) {
  return Object.entries(ctx.reflect.props)
    .filter(([, descriptor]) => descriptor?.type === 'service')
    .map(([name]) => name)
    .sort()
}

async function createProvider(root, qualificationHooks) {
  const ctx = new Context()
  await ctx.plugin(FixedRootFileSystem, {
    root,
    ...(qualificationHooks === undefined ? {} : { qualificationHooks }),
  })
  return ctx
}

async function qualification() {
  assertPinnedNodeVersion()
  assertFixedRootFilesystemContract()
  const scratch = await mkdtemp(join(tmpdir(), 'genshin-fixed-root-probe-'))
  const root = join(scratch, 'private-read-mirror')
  const outside = join(scratch, 'outside.bin')
  const filePath = join(root, FILE_LEAF)
  const hardlinkPath = join(root, HARDLINK_LEAF)
  const linkPath = join(root, LINK_LEAF)
  const growthPath = join(root, GROWTH_LEAF)
  const cancelPath = join(root, CANCEL_LEAF)
  const directoryPath = join(root, DIRECTORY_LEAF)
  const nestedPath = join(directoryPath, NESTED_LEAF)
  const rootAlias = join(scratch, 'private-read-mirror-alias')
  const content = Buffer.from('{"safe":true}\n', 'utf8')
  let listenerAttempts = 0
  const originalListen = NetServer.prototype.listen
  NetServer.prototype.listen = function forbiddenListen(..._args) {
    listenerAttempts += 1
    throw new Error('fixed_root_probe_network_listener_forbidden')
  }
  let ctx
  try {
    await mkdir(root, { recursive: true })
    await writeFile(outside, Buffer.from('outside', 'utf8'))
    await writeFile(filePath, content)
    await writeFile(growthPath, Buffer.from('123456', 'utf8'))
    await writeFile(cancelPath, Buffer.from('cancel', 'utf8'))
    await mkdir(directoryPath)
    await writeFile(nestedPath, Buffer.from('nested', 'utf8'))
    ctx = await createProvider(root)
    assert(listenerAttempts === 0, 'fixed_root_probe_listener_attempted')
    assert(JSON.stringify(services(ctx)) === JSON.stringify(['fs']),
      'fixed_root_probe_service_surface')
    assert(ctx.fs.sandboxMode === 'read-only', 'fixed_root_probe_sandbox_mode')

    const virtualPath = `${FIXED_ROOT_VIRTUAL_ROOT}/${FILE_LEAF}`
    const rootTarget = await ctx.fs.resolve(FIXED_ROOT_VIRTUAL_ROOT)
    const target = await ctx.fs.resolve(virtualPath)
    const samePathTarget = await ctx.fs.resolve(virtualPath)
    assert(samePathTarget !== target
      && samePathTarget.targetKey === target.targetKey,
    'fixed_root_probe_unbounded_leaf_cache')
    assert(!Object.hasOwn(ctx.fs, '_leafTargets'),
      'fixed_root_probe_leaf_cache_present')
    for (const exposedCapabilityMember of [
      '_root',
      '_targets',
      '_rootTarget',
      '_assertRoot',
      '_record',
      '_target',
      '_pathInfo',
      '_afterReadHandleOpened',
    ]) {
      assert(!(exposedCapabilityMember in ctx.fs),
        `fixed_root_probe_private_member_exposed:${exposedCapabilityMember}`)
    }
    assert(Object.isFrozen(target), 'fixed_root_probe_target_mutable')
    assert(target.displayPath === virtualPath, 'fixed_root_probe_display_path')
    assert(!JSON.stringify(target).includes(root), 'fixed_root_probe_root_disclosed')
    assert(ctx.fs.processPath(target) === virtualPath,
      'fixed_root_probe_process_path')
    assert(ctx.fs.processPathFromHostPath(filePath) === virtualPath,
      'fixed_root_probe_host_mapping')
    assert(ctx.fs.processPathFromHostPath(outside) === undefined,
      'fixed_root_probe_outside_mapping')
    assert(ctx.fs.processPathFromHostPath(root) === undefined,
      'fixed_root_probe_root_mapping')
    assert(ctx.fs.fileUrl(target) === `file://${virtualPath}`,
      'fixed_root_probe_file_url')
    assert(ctx.fs.contains(rootTarget, target) === true,
      'fixed_root_probe_root_contains_file')
    assert(ctx.fs.contains(target, target) === true,
      'fixed_root_probe_file_contains_self')
    assert(ctx.fs.contains(target, rootTarget) === false,
      'fixed_root_probe_file_contains_root')
    assert(ctx.fs.contains(target, { ...target }) === false,
      'fixed_root_probe_forged_containment')
    const rootInfo = await ctx.fs.stat(rootTarget)
    const info = await ctx.fs.stat(target)
    const pathInfo = await ctx.fs.lstat(virtualPath)
    assert(rootInfo?.type === 'directory', 'fixed_root_probe_root_stat')
    assert(info?.type === 'file' && info.size === content.length,
      'fixed_root_probe_file_stat')
    assert(pathInfo?.type === 'file' && pathInfo.size === content.length,
      'fixed_root_probe_file_lstat')
    const missingPath = `${FIXED_ROOT_VIRTUAL_ROOT}/${MISSING_LEAF}`
    const missingTarget = await ctx.fs.resolve(missingPath)
    assert(await ctx.fs.stat(missingTarget) === undefined,
      'fixed_root_probe_missing_stat')
    assert(await ctx.fs.lstat(missingPath) === undefined,
      'fixed_root_probe_missing_lstat')
    await expectFsError(
      () => ctx.fs.readBytes(missingTarget, undefined, 1),
      'FS_NOT_FOUND',
    )
    const directoryTarget = await ctx.fs.resolve(
      `${FIXED_ROOT_VIRTUAL_ROOT}/${DIRECTORY_LEAF}`,
    )
    await expectFsError(() => ctx.fs.stat(directoryTarget), 'FS_PERMISSION_DENIED')
    assert(ctx.fs.processPathFromHostPath(nestedPath) === undefined,
      'fixed_root_probe_nested_mapping')
    const bytes = await ctx.fs.readBytes(target, undefined, content.length)
    assert(Buffer.from(bytes).equals(content), 'fixed_root_probe_read_bytes')
    await expectFsError(
      () => ctx.fs.readBytes(target, undefined, content.length - 1),
      'FS_TOO_LARGE',
    )
    const cancelled = new AbortController()
    cancelled.abort()
    await expectFsError(
      () => ctx.fs.readBytes(target, cancelled.signal, content.length),
      'FS_ABORTED',
    )
    await expectFsError(
      () => ctx.fs.readBytes(rootTarget, undefined, 1),
      'FS_NOT_REGULAR_FILE',
    )
    await expectFsError(
      () => ctx.fs.readBytes(target, undefined, -1),
      'FS_PERMISSION_DENIED',
    )
    await expectFsError(
      () => ctx.fs.resolve(virtualPath, { cwd: FIXED_ROOT_VIRTUAL_ROOT }),
      'FS_PERMISSION_DENIED',
    )
    await expectFsError(() => ctx.fs.stat({ ...target }), 'FS_PERMISSION_DENIED')

    for (const rejected of [
      filePath,
      outside,
      FILE_LEAF,
      `../${FILE_LEAF}`,
      `${FIXED_ROOT_VIRTUAL_ROOT}/../${FILE_LEAF}`,
      `${FIXED_ROOT_VIRTUAL_ROOT}/${FILE_LEAF}%2e`,
      `${FIXED_ROOT_VIRTUAL_ROOT}\\${FILE_LEAF}`,
      `/unreviewed/${FILE_LEAF}`,
      `${FIXED_ROOT_VIRTUAL_ROOT}/CON`,
      `${FIXED_ROOT_VIRTUAL_ROOT}/${'f'.repeat(64)}.json/child`,
    ]) {
      const error = await expectFsError(
        () => ctx.fs.resolve(rejected),
        'FS_PERMISSION_DENIED',
      )
      assert(!error.message.includes(root), 'fixed_root_probe_error_disclosed_root')
    }

    await expectFsError(() => ctx.fs.readText(target), 'FS_PERMISSION_DENIED')
    await expectFsError(() => ctx.fs.streamText(target), 'FS_PERMISSION_DENIED')
    await expectFsError(
      () => ctx.fs.readByteRange(target, { offset: 0, length: 1 }),
      'FS_PERMISSION_DENIED',
    )
    await expectFsError(() => ctx.fs.listDir(rootTarget), 'FS_PERMISSION_DENIED')
    await expectFsError(() => ctx.fs.writeText(target, 'x'), 'FS_PERMISSION_DENIED')
    await expectFsError(
      () => ctx.fs.editText(target, {
        oldString: 'x', newString: 'y', replaceAll: false,
      }),
      'FS_PERMISSION_DENIED',
    )
    await expectFsError(
      () => ctx.fs.watch(target, () => {}, new AbortController().signal),
      'FS_PERMISSION_DENIED',
    )

    await link(outside, hardlinkPath)
    assert(ctx.fs.processPathFromHostPath(hardlinkPath) === undefined,
      'fixed_root_probe_hardlink_host_mapping')
    const hardlinkTarget = await ctx.fs.resolve(
      `${FIXED_ROOT_VIRTUAL_ROOT}/${HARDLINK_LEAF}`,
    )
    await expectFsError(() => ctx.fs.stat(hardlinkTarget), 'FS_PERMISSION_DENIED')

    let symlinkRejected = false
    try {
      await symlink(outside, linkPath, process.platform === 'win32' ? 'file' : undefined)
      const linkTarget = await ctx.fs.resolve(
        `${FIXED_ROOT_VIRTUAL_ROOT}/${LINK_LEAF}`,
      )
      await expectFsError(() => ctx.fs.stat(linkTarget), 'FS_PERMISSION_DENIED')
      symlinkRejected = true
    } catch (error) {
      if (!(error instanceof Error)
          || !('code' in error)
          || !['EPERM', 'EACCES', 'UNKNOWN'].includes(error.code)) {
        throw error
      }
    }

    const growthCtx = await createProvider(root, {
      async afterReadHandleOpened({ leaf }) {
        if (leaf === GROWTH_LEAF) {
          await appendFile(growthPath, Buffer.from('7', 'utf8'))
        }
      },
    })
    try {
      const growthTarget = await growthCtx.fs.resolve(
        `${FIXED_ROOT_VIRTUAL_ROOT}/${GROWTH_LEAF}`,
      )
      await expectFsError(
        () => growthCtx.fs.readBytes(growthTarget, undefined, 6),
        'FS_TOO_LARGE',
      )
    } finally {
      await growthCtx.fiber.dispose()
    }

    const midReadController = new AbortController()
    const cancelCtx = await createProvider(root, {
      async afterReadHandleOpened({ leaf }) {
        if (leaf === CANCEL_LEAF) midReadController.abort()
      },
    })
    try {
      const cancelTarget = await cancelCtx.fs.resolve(
        `${FIXED_ROOT_VIRTUAL_ROOT}/${CANCEL_LEAF}`,
      )
      await expectFsError(
        () => cancelCtx.fs.readBytes(cancelTarget, midReadController.signal, 6),
        'FS_ABORTED',
      )
    } finally {
      await cancelCtx.fiber.dispose()
    }

    await ctx.fiber.dispose()
    ctx = undefined
    const restarted = await createProvider(root)
    try {
      const coldTarget = await restarted.fs.resolve(virtualPath)
      assert(coldTarget.targetKey === target.targetKey,
        'fixed_root_probe_target_key_not_stable')
    } finally {
      await restarted.fiber.dispose()
    }

    let rootLinkRejected = false
    try {
      await symlink(
        root,
        rootAlias,
        process.platform === 'win32' ? 'junction' : 'dir',
      )
      let aliasCtx
      try {
        aliasCtx = await createProvider(rootAlias)
      } catch (error) {
        rootLinkRejected = error instanceof TypeError
          && error.message === 'fixed_root_filesystem_root_invalid'
      } finally {
        await aliasCtx?.fiber.dispose().catch(() => {})
      }
      assert(rootLinkRejected, 'fixed_root_probe_root_link_admitted')
    } catch (error) {
      if (!(error instanceof Error)
          || !('code' in error)
          || !['EPERM', 'EACCES', 'UNKNOWN'].includes(error.code)) {
        throw error
      }
    }

    return Object.freeze({
      contract: FIXED_ROOT_FILESYSTEM_CONTRACT,
      contractVersion: FIXED_ROOT_FILESYSTEM_VERSION,
      status: 'passed',
      productionOwner: false,
      virtualRoot: FIXED_ROOT_VIRTUAL_ROOT,
      policy: FIXED_ROOT_POLICY,
      supportedMethods: FIXED_ROOT_SUPPORTED_METHODS,
      rejectedMethods: FIXED_ROOT_REJECTED_METHODS,
      forbiddenCapabilities: FIXED_ROOT_FORBIDDEN_CAPABILITIES,
      activeServices: ['fs'],
      listenerAttempts,
      targetKeyStableAcrossRestart: true,
      unboundedLeafCache: false,
      privateCapabilityState: true,
      realHostPathRejected: true,
      outsideMappingRejected: true,
      forgedTargetRejected: true,
      hardlinkRejected: true,
      symlinkRejected,
      rootLinkRejected,
      unclassifiedWindowsReparsePoints:
        'host-mirror-prevalidation-required',
      boundedRead: true,
      growthRejected: true,
      preAbortRejected: true,
      midReadAbortRejected: true,
      rootDisclosureDetected: false,
      sessionControllerIntegrated: false,
    })
  } finally {
    NetServer.prototype.listen = originalListen
    await ctx?.fiber.dispose().catch(() => {})
    await rm(scratch, { recursive: true, force: true }).catch(() => {})
  }
}

const result = await qualification()
process.stdout.write(`${JSON.stringify(result, null, 2)}\n`)
