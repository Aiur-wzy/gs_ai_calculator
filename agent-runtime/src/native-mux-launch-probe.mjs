/** Offline cross-language qualification probe for the mux-only launch v1. */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

import {
  MUX_LAUNCH_CONTRACT,
  MUX_TRANSPORT_CODEC,
  NativeMuxLaunchError,
  PRODUCTION_OWNER,
  decodeMuxLaunchContract,
  encodeMuxLaunchContract,
} from './native-mux-launch.mjs'

const fixture = JSON.parse(await readFile(
  new URL('../native-mux-launch-v1-vectors.json', import.meta.url),
  'utf8',
))

function rejects(code, operation) {
  let caught
  try {
    operation()
  } catch (error) {
    caught = error
  }
  assert.ok(caught instanceof NativeMuxLaunchError)
  assert.equal(caught.code, code)
  return caught
}

const expected = Buffer.from(fixture.packet_base64, 'base64')
assert.deepEqual(encodeMuxLaunchContract(fixture.node_input), expected)
assert.deepEqual(decodeMuxLaunchContract(expected), fixture.node_output)

rejects('mux_launch_transport_codec', () => encodeMuxLaunchContract({
  ...fixture.node_input,
  transportCodec: 'genshin-native-launch-v1',
}))
const legacy = Buffer.from(fixture.legacy_packet_base64, 'base64')
rejects('mux_launch_contract_shape', () => decodeMuxLaunchContract(legacy))
const noncanonical = Buffer.from(expected)
const text = noncanonical.subarray(4).toString('utf8')
const spaced = Buffer.from(JSON.stringify(JSON.parse(text), null, 1), 'utf8')
const spacedPacket = Buffer.allocUnsafe(4 + spaced.length)
spacedPacket.writeUInt32BE(spaced.length)
spaced.copy(spacedPacket, 4)
rejects('mux_launch_contract_noncanonical', () => decodeMuxLaunchContract(spacedPacket))
const bomBody = Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), expected.subarray(4)])
const bomPacket = Buffer.allocUnsafe(4 + bomBody.length)
bomPacket.writeUInt32BE(bomBody.length)
bomBody.copy(bomPacket, 4)
rejects('mux_launch_contract_json', () => decodeMuxLaunchContract(bomPacket))

process.stdout.write(`${JSON.stringify({
  contract: MUX_LAUNCH_CONTRACT,
  transportCodec: MUX_TRANSPORT_CODEC,
  productionOwner: PRODUCTION_OWNER,
  status: 'passed',
})}\n`)
