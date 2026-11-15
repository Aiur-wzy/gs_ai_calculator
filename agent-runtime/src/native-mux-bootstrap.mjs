/** One inherited mux-only launch, selected before interpreting any pipe byte. */
import { readSync } from 'node:fs'
import { createConnection } from 'node:net'
import { decodeMuxLaunchContract, MAX_MUX_LAUNCH_BYTES } from './native-mux-launch.mjs'
import { NativeMuxPipeClient } from './native-mux-pipe-client.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'

export const PRODUCTION_OWNER = false

function readExact(fd, size) {
  const bytes = Buffer.alloc(size)
  let offset = 0
  while (offset < size) {
    const count = readSync(fd, bytes, offset, size - offset, null)
    if (count === 0) throw new Error('mux_bootstrap_truncated')
    offset += count
  }
  return bytes
}

export async function connectInheritedMux({ fd = 0, connectTimeoutMs = 15_000 } = {}) {
  assertPinnedNodeVersion()
  const prefix = readExact(fd, 4)
  const size = prefix.readUInt32BE(0)
  if (size < 1 || size > MAX_MUX_LAUNCH_BYTES) throw new Error('mux_bootstrap_size')
  const launch = decodeMuxLaunchContract(Buffer.concat([prefix, readExact(fd, size)]))
  const socket = createConnection(launch.pipeName)
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('mux_bootstrap_connect_timeout')), connectTimeoutMs)
      const onError = () => reject(new Error('mux_bootstrap_connect_failed'))
      const onConnect = () => resolve()
      socket.once('error', onError)
      socket.once('connect', onConnect)
      socket.once('close', () => clearTimeout(timer))
      socket.once('connect', () => clearTimeout(timer))
    })
    const carrier = new NativeMuxPipeClient(socket, {
      senderRole: 'runtime', peerSenderRole: 'host',
      connectionGeneration: launch.connectionGeneration,
      connectionId: launch.connectionId,
      runtimeIncarnation: launch.runtimeIncarnation,
    })
    carrier.enqueue({ lane: 'control', payloadKind: 'dsh_mux_hello',
      payload: Buffer.from(JSON.stringify({ contract: 'genshin-native-dsh-mux-hello-v1',
        nonce: launch.nonce, nodeVersion: process.versions.node }), 'utf8'),
      messageId: 'dsh-mux-hello', trustedClass: 'control',
    })
    await carrier.writeNext()
    return { carrier, launch }
  } catch (error) {
    socket.destroy()
    throw error
  }
}
