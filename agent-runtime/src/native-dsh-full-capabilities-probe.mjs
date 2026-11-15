/** Offline qualification of real upstream FS, Windows shell, App CLI and durable notes. */
import assert from 'node:assert/strict'
import {mkdtemp,readFile,rm} from 'node:fs/promises'
import {join,resolve,dirname,basename} from 'node:path'
import {fileURLToPath} from 'node:url'
import {assertPinnedNodeVersion} from './runtime-version.mjs'
assertPinnedNodeVersion()
import {tmpdir} from 'node:os'
import {createNativeDshSessionHost} from './native-dsh-session-host.mjs'
const root=await mkdtemp(join(tmpdir(),'genshin-r14-full-tools-'))
const source=resolve(dirname(fileURLToPath(import.meta.url)),'../../src').replaceAll("'","''")
let host
let calls=0
const originalFetch=globalThis.fetch
globalThis.fetch=()=>{throw new Error('network forbidden in offline tools qualification')}
const evidence={profileRoot:root}
try {
 host=await createNativeDshSessionHost({profileRoot:root,mirrorRoot:join(root,'media')})
 const {agent}=await host.context.agents.create({sessionId:'actual-fs-shell',meta:{cwd:host.kernel.fullProfile.workspace}})
 const execute=(name,args)=>host.context.tools.execute({callId:`actual-${++calls}`,name,arguments:args,agent,signal:new AbortController().signal})
 const check=r=>{assert.equal(r.isError,false,JSON.stringify(r));return r}
 check(await execute('write',{file_path:'character-note.txt',content:'facts: original\n'}))
 check(await execute('read',{file_path:'character-note.txt'}))
 check(await execute('edit',{file_path:'character-note.txt',old_string:'original',new_string:'verified'}))
 assert.equal(await readFile(join(host.kernel.fullProfile.workspace,'character-note.txt'),'utf8'),'facts: verified\n')
 evidence.fileCreateUpdateRead=true
 const result=check(await execute('pwsh',{description:'Run the actual App CLI capabilities offline',command:`$env:PYTHONPATH='${source}'; python -m genshin_app --data-dir '${join(root,'workspace','cli-data')}' capabilities --command 'equipment edit'`}))
 assert.equal(result.value.exitCode,0,JSON.stringify(result))
 assert.match(result.value.stdout.text,/equipment/)
 evidence.pwshSandbox=result.value.sandbox
 evidence.actualCliThroughPwsh=true
 await host.kernel.flushSessions()
 await host.dispose();host=undefined
 host=await createNativeDshSessionHost({profileRoot:root,mirrorRoot:join(root,'media')})
 const restored=await host.context.agents.resume({resumeSessionId:'actual-fs-shell'})
 const reread=await host.context.tools.execute({callId:'after-cold-restore',name:'read',arguments:{file_path:'character-note.txt'},agent:restored.agent,signal:new AbortController().signal})
 check(reread)
 assert.match(JSON.stringify(reread),/verified/)
 evidence.coldRestoreNotes=true
 console.log(JSON.stringify({contract:'genshin-native-full-capabilities-v1',status:'passed',network_requests:0,...evidence}))
}finally{
 await host?.dispose();globalThis.fetch=originalFetch
 const target=resolve(root)
 if(dirname(target)!==resolve(tmpdir())||!basename(target).startsWith('genshin-r14-full-tools-')) throw new Error('unsafe test cleanup target')
 await rm(target,{recursive:true,force:true})
}


