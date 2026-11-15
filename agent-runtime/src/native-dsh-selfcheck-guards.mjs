/** Application isolation for the fixed packaged CLI qualification, not a sandbox service. */
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import net from 'node:net'
import tls from 'node:tls'
import http from 'node:http'
import https from 'node:https'
import dns from 'node:dns'
import dgram from 'node:dgram'
import childProcess from 'node:child_process'
import { syncBuiltinESMExports } from 'node:module'
import { basename, dirname, isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export function installSelfcheckGuards({ sandboxRoot, packageRoot }) {
  const sandbox = resolve(sandboxRoot), packaged = resolve(packageRoot)
  const originalRealpath = fs.realpathSync.native
  const counts = { normal: {}, negative: {} }
  const denied = []
  let negative = false
  const deny = (kind, path = null) => {
    const group = counts[negative ? 'negative' : 'normal']
    group[kind] = (group[kind] ?? 0) + 1
    if (!negative) denied.push({ kind, path, frames: new Error().stack?.split('\n').slice(2, 9) })
    throw Object.assign(new Error(`selfcheck_${kind}_denied`), { code: `selfcheck_${kind}_denied` })
  }
  const within = (path, root) => {
    const suffix = relative(root, path)
    return suffix === '' || (!suffix.startsWith('..') && !isAbsolute(suffix))
  }
  function canonical(value) {
    let path = value instanceof URL ? fileURLToPath(value) : Buffer.isBuffer(value) ? value.toString() : value
    if (typeof path !== 'string') return null
    path = resolve(path)
    // Resolve existing ancestors so a junction cannot turn an apparently
    // private destination into a write outside the admitted root.
    let parent = path
    const suffix = []
    while (true) {
      try { return resolve(originalRealpath(parent), ...suffix) } catch {
        const next = dirname(parent)
        if (next === parent) return path
        suffix.unshift(basename(parent))
        parent = next
      }
    }
  }
  const check = (value, write = false) => {
    const path = canonical(value)
    if (path === null) return
    if (!(within(path, sandbox) || (!write && within(path, packaged)))) deny(write ? 'outside_write' : 'outside_read', path)
  }
  const writing = flags => typeof flags === 'number'
    ? Boolean(flags & (fs.constants.O_WRONLY | fs.constants.O_RDWR | fs.constants.O_CREAT | fs.constants.O_TRUNC | fs.constants.O_APPEND))
    : /[wa+]/u.test(flags ?? 'r')
  const wrap = (owner, name, checks) => {
    const original = owner[name]
    if (typeof original !== 'function') return
    owner[name] = function (...args) { checks(args); return Reflect.apply(original, this, args) }
  }
  for (const owner of [fs, fsp]) {
    for (const name of ['readFile', 'readFileSync', 'readdir', 'readdirSync', 'createReadStream']) wrap(owner, name, args => check(args[0]))
    for (const name of ['writeFile', 'writeFileSync', 'appendFile', 'appendFileSync', 'mkdir', 'mkdirSync', 'mkdtemp', 'mkdtempSync',
      'unlink', 'unlinkSync', 'rm', 'rmSync', 'rmdir', 'rmdirSync', 'truncate', 'truncateSync', 'chmod', 'chmodSync', 'utimes', 'utimesSync', 'createWriteStream']) {
      wrap(owner, name, args => check(args[0], true))
    }
    for (const name of ['open', 'openSync']) wrap(owner, name, args => check(args[0], writing(args[1])))
    for (const name of ['copyFile', 'copyFileSync', 'cp', 'cpSync', 'link', 'linkSync', 'symlink', 'symlinkSync']) {
      wrap(owner, name, args => { check(args[0]); check(args[1], true) })
    }
    for (const name of ['rename', 'renameSync']) wrap(owner, name, args => { check(args[0], true); check(args[1], true) })
  }
  const privatePipe = args => {
    const selected = typeof args[0] === 'object' ? args[0]?.path : args[0]
    if (typeof selected !== 'string' || !/^\\\\\.\\pipe\\genshin-native-[0-9a-f]{64}$/u.test(selected)) deny('network')
  }
  wrap(net, 'connect', privatePipe)
  wrap(net, 'createConnection', privatePipe)
  wrap(net.Socket.prototype, 'connect', args => {
    // Node normalizes connect arguments into a single array internally.
    privatePipe(Array.isArray(args[0]) ? args[0] : args)
  })
  for (const owner of [http, https, tls, dns, dns.promises, dgram]) {
    for (const name of ['request', 'get', 'connect', 'lookup', 'resolve', 'resolve4', 'resolve6', 'createSocket']) {
      wrap(owner, name, () => deny('network'))
    }
  }
  for (const name of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) wrap(childProcess, name, () => deny('process'))
  syncBuiltinESMExports()
  return Object.freeze({ counts, denied,
    denyCredential: () => deny('credential'),
    denyNetwork: () => deny('network'),
    assertClean() {
      if (Object.values(counts.normal).some(Boolean)) throw new Error('selfcheck_normal_runtime_forbidden_attempt')
    },
    negativeProbe(operation) {
      negative = true
      try { operation() } catch (error) {
        if (!String(error?.code).startsWith('selfcheck_')) throw error
      } finally { negative = false }
    },
    async negativeProbeAsync(operation) {
      negative = true
      try { await operation() } catch (error) {
        if (!String(error?.code).startsWith('selfcheck_')) throw error
      } finally { negative = false }
    },
  })
}
