/** Test-host selection only. Never registered or bundled into the App. */
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {lstatSync,readFileSync,realpathSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fail=code=>{throw Object.assign(new Error(code),{code});};
const same=(a,b)=>process.platform==='win32'?a.toLowerCase()===b.toLowerCase():a===b;
export function selectTestNode(environment=process.env,executable=process.execPath){
  return resolve(environment.GENSHIN_NATIVE_NODE?.trim()||executable);
}
export function readIndependentNodePins(){
  const script="import json,sys;from pathlib import Path;sys.path.insert(0,str(Path(sys.argv[1])/'src'));from genshin_app import version;assert Path(version.__file__).resolve()==(Path(sys.argv[1])/'src/genshin_app/version.py').resolve();print(json.dumps({'version':version.NATIVE_AGENT_NODE_VERSION,'sha256':version.NATIVE_AGENT_NODE_BINARY_SHA256}))";
  const pins=JSON.parse(execFileSync('python',['-c',script,root],{cwd:root,encoding:'utf8',windowsHide:true}));
  if(!/^\d+\.\d+\.\d+$/.test(pins.version)||!(/^[0-9a-f]{64}$/).test(pins.sha256))fail('pinned_test_node_identity_invalid');
  return pins;
}
export function verifyTestNode(node,pins){
  const path=resolve(node),info=lstatSync(path);
  if(!info.isFile()||info.isSymbolicLink()||!same(realpathSync(path),path))fail('pinned_test_node_alias_or_type');
  if(createHash('sha256').update(readFileSync(path)).digest('hex')!==pins.sha256)fail('pinned_test_node_sha_mismatch');
  const version=execFileSync(path,['--version'],{encoding:'utf8',windowsHide:true,timeout:30000}).trim();
  if(version!==`v${pins.version}`)fail('pinned_test_node_version_mismatch');
  return path;
}
export function pinnedTestNode(){return verifyTestNode(selectTestNode(),readIndependentNodePins());}
