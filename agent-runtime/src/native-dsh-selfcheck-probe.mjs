/** Source-only unit probe; never grants frozen package qualification. */
import assert from 'node:assert/strict'
import fs from 'node:fs'
import net from 'node:net'
import http from 'node:http'
import childProcess from 'node:child_process'
import { resolve, join } from 'node:path'
import { installSelfcheckGuards } from './native-dsh-selfcheck-guards.mjs'
import { selfcheckProvider } from './native-dsh-selfcheck-provider.mjs'

const root = resolve(process.argv[2]), packaged = resolve(process.argv[3])
fs.mkdirSync(root, { recursive: true })
const guards = installSelfcheckGuards({ sandboxRoot: root, packageRoot: packaged })
guards.negativeProbe(() => fs.writeFileSync(resolve(root, '..', 'outside-must-not-exist'), 'never'))
guards.negativeProbe(() => fs.readFileSync(resolve(packaged, '..', 'outside-must-not-exist')))
guards.negativeProbe(() => net.connect({ host: '127.0.0.1', port: 9 }))
guards.negativeProbe(() => http.get('http://127.0.0.1:9/'))
guards.negativeProbe(() => childProcess.spawn('must-not-execute'))
guards.negativeProbe(() => guards.denyCredential())
const textPath = join(root, 'allowed.json')
fs.writeFileSync(textPath, '{"inside":true}')
assert.equal(JSON.parse(fs.readFileSync(textPath, 'utf8')).inside, true)
const provider = selfcheckProvider([{ tool: 'gs_read_context', arguments: { resource: 'capability_index' } }], guards)
const init = { method: 'POST', body: JSON.stringify({ tools: [{ name: 'gs_read_context', input_schema: {
  type: 'object', properties: { resource: { type: 'string' } }, required: ['resource'], additionalProperties: false,
} }] }) }
assert.match(await (await provider('https://api.deepseek.com/anthropic/v1/messages', init)).text(), /tool_use/u)
assert.match(await (await provider('https://api.deepseek.com/anthropic/v1/messages', init)).text(), /end_turn/u)
await guards.negativeProbeAsync(() => provider('https://example.test/forbidden', init))
assert.equal(provider.requestCount, 2)
guards.assertClean()
assert.equal(guards.counts.negative.outside_write, 1)
assert.equal(guards.counts.negative.outside_read, 1)
assert.equal(guards.counts.negative.network, 3)
assert.equal(guards.counts.negative.process, 1)
assert.equal(guards.counts.negative.credential, 1)
console.log(JSON.stringify({ status: 'verified_source_probe', actual_frozen_tested: false, counters: guards.counts }))
