/**
 * App-private, read-only DSH filesystem for reviewed session media.
 *
 * The provider deliberately exposes a synthetic absolute namespace while the
 * real mirror root remains a Host-only implementation detail.  It is not a
 * general filesystem tool and cannot be widened by an approval or sandbox
 * setting.
 */
import { createHash } from 'node:crypto'
import {
  closeSync,
  fstatSync,
  lstatSync,
  openSync,
  realpathSync,
} from 'node:fs'
import {
  lstat,
  open,
  realpath,
} from 'node:fs/promises'
import {
  dirname,
  isAbsolute,
  join,
  relative,
  resolve as resolvePath,
  sep,
} from 'node:path'

import {
  FileSystem,
  FsError,
  FsTargetKey,
  FsVersion,
} from '@deepseek-ai/dsh-fs'

import {
  FIXED_ROOT_LEAF_PATTERN,
  FIXED_ROOT_VIRTUAL_ROOT,
  assertFixedRootFilesystemContract,
} from './fixed-root-filesystem-contract.mjs'

const READ_CHUNK_BYTES = 64 * 1024
const ROOT_TARGET_KEY = FsTargetKey('genshin-fixed-root-v1:root')
const WINDOWS_RESERVED = /^(?:aux|con|nul|prn|com[1-9]|lpt[1-9])(?:\.|$)/iu
const PRIVATE_STATE_METHODS = Object.freeze([
  'resolve',
  'processPath',
  'processPathFromHostPath',
  'fileUrl',
  'contains',
  'stat',
  'lstat',
  'readBytes',
])

function fail(message, code) {
  throw new FsError(message, code)
}

function throwIfAborted(signal) {
  if (signal?.aborted) fail('fixed-root read aborted', 'FS_ABORTED')
}

function isMissing(error) {
  return error instanceof Error
    && 'code' in error
    && (error.code === 'ENOENT' || error.code === 'ENOTDIR')
}

function isPermission(error) {
  return error instanceof Error
    && 'code' in error
    && (error.code === 'EACCES' || error.code === 'EPERM')
}

function translateIo(error, operation) {
  if (error instanceof FsError) return error
  if (error instanceof Error && error.name === 'AbortError') {
    return new FsError(`fixed-root ${operation} aborted`, 'FS_ABORTED')
  }
  if (isMissing(error)) {
    return new FsError(`fixed-root ${operation} target is absent`, 'FS_NOT_FOUND')
  }
  if (isPermission(error)) {
    return new FsError(`fixed-root ${operation} was denied`, 'FS_PERMISSION_DENIED')
  }
  return new FsError(`fixed-root ${operation} failed`, 'FS_IO_ERROR')
}

function canonicalComparable(path) {
  const value = resolvePath(path)
  return process.platform === 'win32' ? value.toLowerCase() : value
}

function samePath(left, right) {
  return canonicalComparable(left) === canonicalComparable(right)
}

function versionOf(info) {
  return FsVersion(`${info.dev}:${info.ino}:${info.size}:${info.mtimeNs}:${info.ctimeNs}`)
}

function sameIdentity(left, right) {
  return left.dev === right.dev && left.ino === right.ino
}

function safeSize(info) {
  if (info.size > BigInt(Number.MAX_SAFE_INTEGER)) {
    fail('fixed-root target size is not representable', 'FS_TOO_LARGE')
  }
  return Number(info.size)
}

function validateRootSync(root) {
  if (typeof root !== 'string' || !isAbsolute(root)) {
    throw new TypeError('fixed_root_filesystem_root_invalid')
  }
  const canonical = resolvePath(root)
  let descriptor
  try {
    const info = lstatSync(canonical, { bigint: true })
    if (!info.isDirectory() || info.isSymbolicLink()) {
      throw new TypeError('fixed_root_filesystem_root_invalid')
    }
    const physical = realpathSync.native(canonical)
    if (!samePath(physical, canonical)) {
      throw new TypeError('fixed_root_filesystem_root_invalid')
    }
    descriptor = openSync(canonical, 'r')
    const opened = fstatSync(descriptor, { bigint: true })
    const after = lstatSync(canonical, { bigint: true })
    if (!sameIdentity(info, opened) || !sameIdentity(opened, after)) {
      throw new TypeError('fixed_root_filesystem_root_invalid')
    }
    return Object.freeze({
      canonical,
      dev: info.dev,
      ino: info.ino,
    })
  } catch (error) {
    if (error instanceof TypeError
        && error.message === 'fixed_root_filesystem_root_invalid') {
      throw error
    }
    throw new TypeError('fixed_root_filesystem_root_invalid')
  } finally {
    if (descriptor !== undefined) closeSync(descriptor)
  }
}

function validLeaf(leaf) {
  return FIXED_ROOT_LEAF_PATTERN.test(leaf)
    && !WINDOWS_RESERVED.test(leaf)
    && !leaf.endsWith('.')
    && !leaf.endsWith(' ')
    && !leaf.includes('%')
    && !leaf.includes(':')
    && !leaf.includes('\\')
    && !/[\u0000-\u001f\u007f]/u.test(leaf)
}

function parseVirtualPath(path) {
  if (typeof path !== 'string' || path === '') {
    fail('fixed-root path is invalid', 'FS_PERMISSION_DENIED')
  }
  if (path === FIXED_ROOT_VIRTUAL_ROOT) {
    return Object.freeze({ kind: 'root', leaf: null, virtualPath: path })
  }
  const prefix = `${FIXED_ROOT_VIRTUAL_ROOT}/`
  if (!path.startsWith(prefix)) {
    fail('fixed-root path is outside the private namespace', 'FS_PERMISSION_DENIED')
  }
  const leaf = path.slice(prefix.length)
  if (!validLeaf(leaf) || leaf.includes('/')) {
    fail('fixed-root path is not canonical', 'FS_PERMISSION_DENIED')
  }
  return Object.freeze({ kind: 'file', leaf, virtualPath: path })
}

function targetKeyForLeaf(leaf) {
  const digest = createHash('sha256')
    .update('genshin-fixed-root-v1\0', 'utf8')
    .update(leaf, 'utf8')
    .digest('hex')
  return FsTargetKey(`genshin-fixed-root-v1:${digest}`)
}

function unsupported(operation) {
  return new FsError(
    `fixed-root ${operation} is not available`,
    'FS_PERMISSION_DENIED',
  )
}

export class FixedRootFileSystem extends FileSystem {
  #root
  #targets
  #rootTarget
  #afterReadHandleOpened

  constructor(ctx, options = {}) {
    super(ctx)
    assertFixedRootFilesystemContract()
    const root = validateRootSync(options.root)
    const qualificationHook = options.qualificationHooks?.afterReadHandleOpened
    if (qualificationHook !== undefined && typeof qualificationHook !== 'function') {
      throw new TypeError('fixed_root_filesystem_qualification_hook_invalid')
    }
    this.#root = root
    this.#targets = new WeakMap()
    this.#afterReadHandleOpened = qualificationHook
    this.#rootTarget = Object.freeze({
      targetKey: ROOT_TARGET_KEY,
      displayPath: FIXED_ROOT_VIRTUAL_ROOT,
    })
    this.#targets.set(this.#rootTarget, Object.freeze({
      kind: 'root',
      leaf: null,
      virtualPath: FIXED_ROOT_VIRTUAL_ROOT,
      physicalPath: root.canonical,
    }))
    // Cordis exposes services through a Proxy.  Immutable bound methods keep
    // private-field access branded to the provider instance rather than the
    // service Proxy and prevent ordinary assignment from replacing capability
    // methods.  The descriptor remains configurable because the Cordis Proxy
    // wraps calls; same-process extensions remain part of the trusted base.
    for (const method of PRIVATE_STATE_METHODS) {
      Object.defineProperty(this, method, {
        configurable: true,
        enumerable: false,
        value: this[method].bind(this),
        writable: false,
      })
    }
  }

  get sandboxMode() {
    return 'read-only'
  }

  async #assertRoot(signal) {
    throwIfAborted(signal)
    let handle
    try {
      const info = await lstat(this.#root.canonical, { bigint: true })
      const physical = await realpath(this.#root.canonical)
      handle = await open(this.#root.canonical, 'r')
      const opened = await handle.stat({ bigint: true })
      const after = await lstat(this.#root.canonical, { bigint: true })
      if (!info.isDirectory()
          || info.isSymbolicLink()
          || info.dev !== this.#root.dev
          || info.ino !== this.#root.ino
          || !sameIdentity(info, opened)
          || !sameIdentity(opened, after)
          || !samePath(physical, this.#root.canonical)) {
        fail('fixed-root identity changed', 'FS_PERMISSION_DENIED')
      }
      return info
    } catch (error) {
      throw translateIo(error, 'root validation')
    } finally {
      await handle?.close().catch(() => {})
    }
  }

  #record(target) {
    const record = target !== null && typeof target === 'object'
      ? this.#targets.get(target)
      : undefined
    if (record === undefined) {
      fail('fixed-root target was not issued by this provider', 'FS_PERMISSION_DENIED')
    }
    return record
  }

  #target(parsed) {
    if (parsed.kind === 'root') return this.#rootTarget
    const target = Object.freeze({
      targetKey: targetKeyForLeaf(parsed.leaf),
      displayPath: parsed.virtualPath,
    })
    const record = Object.freeze({
      ...parsed,
      physicalPath: join(this.#root.canonical, parsed.leaf),
    })
    this.#targets.set(target, record)
    return target
  }

  async resolve(path, options = {}) {
    throwIfAborted(options.signal)
    if (options.cwd !== undefined) {
      fail('fixed-root relative resolution is disabled', 'FS_PERMISSION_DENIED')
    }
    const parsed = parseVirtualPath(path)
    await this.#assertRoot(options.signal)
    return this.#target(parsed)
  }

  processPath(target) {
    return this.#record(target).virtualPath
  }

  processPathFromHostPath(hostPath) {
    if (typeof hostPath !== 'string' || !isAbsolute(hostPath)) return undefined
    try {
      const rootInfo = validateRootSync(this.#root.canonical)
      if (rootInfo.dev !== this.#root.dev || rootInfo.ino !== this.#root.ino) {
        return undefined
      }
      const canonical = resolvePath(hostPath)
      const child = relative(this.#root.canonical, canonical)
      if (child === ''
          || child === '..'
          || child.startsWith(`..${sep}`)
          || isAbsolute(child)
          || dirname(child) !== '.'
          || !validLeaf(child)) {
        return undefined
      }
      const info = lstatSync(canonical, { bigint: true })
      if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1n) {
        return undefined
      }
      if (!samePath(realpathSync.native(canonical), canonical)) return undefined
      return `${FIXED_ROOT_VIRTUAL_ROOT}/${child}`
    } catch {
      return undefined
    }
  }

  fileUrl(target) {
    const path = this.#record(target).virtualPath
    return new URL(path, 'file:///').href
  }

  contains(parent, child) {
    const parentRecord = parent !== null && typeof parent === 'object'
      ? this.#targets.get(parent)
      : undefined
    const childRecord = child !== null && typeof child === 'object'
      ? this.#targets.get(child)
      : undefined
    if (parentRecord === undefined || childRecord === undefined) return false
    return parentRecord.kind === 'root'
      || parentRecord.virtualPath === childRecord.virtualPath
  }

  async #pathInfo(record, signal) {
    await this.#assertRoot(signal)
    if (record.kind === 'root') {
      const info = await lstat(this.#root.canonical, { bigint: true })
      return Object.freeze({ info, type: 'directory' })
    }
    let info
    try {
      info = await lstat(record.physicalPath, { bigint: true })
    } catch (error) {
      if (isMissing(error)) return undefined
      throw translateIo(error, 'metadata read')
    }
    if (info.isSymbolicLink()
        || !info.isFile()
        || info.nlink !== 1n) {
      fail('fixed-root target is not an approved regular file', 'FS_PERMISSION_DENIED')
    }
    let physical
    try {
      physical = await realpath(record.physicalPath)
    } catch (error) {
      throw translateIo(error, 'metadata read')
    }
    if (!samePath(physical, record.physicalPath)) {
      fail('fixed-root target changed identity', 'FS_PERMISSION_DENIED')
    }
    let after
    try {
      after = await lstat(record.physicalPath, { bigint: true })
    } catch (error) {
      throw translateIo(error, 'metadata read')
    }
    if (!after.isFile()
        || after.isSymbolicLink()
        || after.nlink !== 1n
        || !sameIdentity(info, after)
        || info.size !== after.size
        || info.mtimeNs !== after.mtimeNs
        || info.ctimeNs !== after.ctimeNs) {
      fail('fixed-root target changed during metadata read', 'FS_PERMISSION_DENIED')
    }
    await this.#assertRoot(signal)
    return Object.freeze({ info, type: 'file' })
  }

  async stat(target, signal) {
    throwIfAborted(signal)
    const record = this.#record(target)
    const result = await this.#pathInfo(record, signal)
    if (result === undefined) return undefined
    return Object.freeze({
      version: versionOf(result.info),
      type: result.type,
      ...(result.type === 'file' ? { size: safeSize(result.info) } : {}),
    })
  }

  async lstat(path, options = {}, signal) {
    throwIfAborted(signal)
    if (options.cwd !== undefined) {
      fail('fixed-root relative metadata is disabled', 'FS_PERMISSION_DENIED')
    }
    const parsed = parseVirtualPath(path)
    const record = this.#record(this.#target(parsed))
    const result = await this.#pathInfo(record, signal)
    if (result === undefined) return undefined
    return Object.freeze({
      version: versionOf(result.info),
      type: result.type,
      ...(result.type === 'file' ? { size: safeSize(result.info) } : {}),
    })
  }

  async readBytes(target, signal, maxBytes) {
    throwIfAborted(signal)
    if (!Number.isSafeInteger(maxBytes) || maxBytes < 0) {
      fail('fixed-root read bound is invalid', 'FS_PERMISSION_DENIED')
    }
    const record = this.#record(target)
    if (record.kind !== 'file') {
      fail('fixed-root target is not a regular file', 'FS_NOT_REGULAR_FILE')
    }
    const before = await this.#pathInfo(record, signal)
    if (before === undefined) {
      fail('fixed-root target is absent', 'FS_NOT_FOUND')
    }
    if (before.info.size > BigInt(maxBytes)) {
      fail('fixed-root target exceeds the read bound', 'FS_TOO_LARGE')
    }
    let handle
    try {
      handle = await open(record.physicalPath, 'r')
      const opened = await handle.stat({ bigint: true })
      if (!opened.isFile()
          || opened.nlink !== 1n
          || !sameIdentity(before.info, opened)) {
        fail('fixed-root target changed during open', 'FS_PERMISSION_DENIED')
      }
      if (this.#afterReadHandleOpened !== undefined) {
        await Reflect.apply(this.#afterReadHandleOpened, undefined, [
          Object.freeze({ leaf: record.leaf }),
          signal,
        ])
      }
      throwIfAborted(signal)
      const current = await this.#pathInfo(record, signal)
      if (current === undefined || !sameIdentity(opened, current.info)) {
        fail('fixed-root target changed during read', 'FS_PERMISSION_DENIED')
      }
      const chunks = []
      let total = 0
      while (true) {
        throwIfAborted(signal)
        const remaining = maxBytes - total
        const length = remaining >= READ_CHUNK_BYTES - 1
          ? READ_CHUNK_BYTES
          : remaining + 1
        const buffer = Buffer.allocUnsafe(length)
        const { bytesRead } = await handle.read(buffer, 0, length, total)
        if (bytesRead === 0) break
        total += bytesRead
        if (total > maxBytes) {
          fail('fixed-root target exceeds the read bound', 'FS_TOO_LARGE')
        }
        chunks.push(buffer.subarray(0, bytesRead))
      }
      const after = await handle.stat({ bigint: true })
      if (!after.isFile()
          || after.nlink !== 1n
          || !sameIdentity(opened, after)
          || opened.size !== after.size
          || opened.mtimeNs !== after.mtimeNs
          || opened.ctimeNs !== after.ctimeNs) {
        fail('fixed-root target changed during read', 'FS_IO_ERROR')
      }
      const finalPath = await this.#pathInfo(record, signal)
      if (finalPath === undefined || !sameIdentity(after, finalPath.info)) {
        fail('fixed-root target changed after read', 'FS_PERMISSION_DENIED')
      }
      return Buffer.concat(chunks, total)
    } catch (error) {
      throw translateIo(error, 'read')
    } finally {
      await handle?.close().catch(() => {})
    }
  }

  readText() {
    return Promise.reject(unsupported('text read'))
  }

  streamText() {
    return Promise.reject(unsupported('text streaming'))
  }

  readByteRange() {
    return Promise.reject(unsupported('range read'))
  }

  listDir() {
    return Promise.reject(unsupported('directory listing'))
  }

  writeText() {
    return Promise.reject(unsupported('write'))
  }

  editText() {
    return Promise.reject(unsupported('edit'))
  }

  watch() {
    return Promise.reject(unsupported('watch'))
  }
}

export default FixedRootFileSystem
