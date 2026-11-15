/** Build only App code against DSH's public ClientBundleRegistration contract. */
import {createHash} from 'node:crypto'
import {readFile, writeFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import {build} from 'vite'
import react from '@vitejs/plugin-react'

const frontend = fileURLToPath(new URL('..', import.meta.url))
const packageDir = fileURLToPath(new URL('../packages/dsh-client-domain', import.meta.url))
const manifest = JSON.parse(await readFile(`${packageDir}/package.json`, 'utf8'))
const dependencies = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8')).dependencies
// This table is pinned against the public PLATFORM_MODULES export by the
// composition tests. Importing the browser boot kernel in plain Node would
// execute its CSS imports; the builder consumes the reviewed contract instead.
const {platformModules,dynamicModules} = JSON.parse(await readFile(new URL('../dsh-client-composition.json', import.meta.url), 'utf8'))
const requested=manifest.dsh.client.external||[]
if(requested.some(name=>!name.endsWith('/client')||!dynamicModules.includes(name.slice(0,-7))))throw new Error('dsh_domain_unreviewed_client_request')
const externals=[...platformModules,...requested]
const id = manifest.name

if (id !== '@genshin-calc/dsh-client-domain') throw new Error('dsh_domain_package_identity_invalid')
const artifacts = await build({
  configFile: false,
  root: frontend,
  plugins: [react()],
  build: {
    write: true,
    outDir: `${packageDir}/lib`,
    emptyOutDir: false,
    minify: false,
    sourcemap: true,
    rollupOptions: {
      input: `${packageDir}/client.ts`,
      preserveEntrySignatures: 'strict',
      external: externals,
      output: {
        format: 'cjs',
        exports: 'named',
        entryFileNames: 'client.js',
        inlineDynamicImports: true,
        banner: `window.__ModuleLoader__.load({id:${JSON.stringify(id)},factory:function(require){var module={exports:{}};var exports=module.exports;`,
        footer: 'return module.exports;}});',
      },
    },
  },
})
if (Array.isArray(artifacts) || !('output' in artifacts)) throw new Error('dsh_domain_build_shape_invalid')
const chunks = artifacts.output.filter(row => row.type === 'chunk')
if (chunks.length !== 1 || chunks[0].fileName !== 'client.js') throw new Error('dsh_domain_build_chunks_invalid')
const entry = chunks[0]
if (entry.imports.some(name => !externals.includes(name))) throw new Error('dsh_domain_unreviewed_external')
if (entry.moduleIds.some(name => name.includes('node_modules'))) throw new Error('dsh_domain_inlined_dependency')
const bytes = Buffer.from(entry.code)
await writeFile(`${packageDir}/lib/build-manifest.json`, `${JSON.stringify({
  contract: 'genshin-dsh-domain-client-build-v1',
  package: id,
  version: manifest.version,
  dshRelease: dependencies['@deepseek-ai/dsh-client-modules'],
  loaderContract: '@deepseek-ai/dsh-client-modules/client:ClientBundleRegistration',
  external: entry.imports,
  injected: manifest.dsh.client.inject,
  bytes: bytes.length,
  sha256: createHash('sha256').update(bytes).digest('hex'),
}, null, 2)}\n`)
