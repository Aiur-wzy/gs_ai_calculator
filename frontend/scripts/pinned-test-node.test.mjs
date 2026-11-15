import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtempSync,writeFileSync,symlinkSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {pinnedTestNode,readIndependentNodePins,selectTestNode,verifyTestNode} from './pinned-test-node.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
test('test host uses explicit path then the running executable without a PATH or old-package search',()=>{
  assert.equal(selectTestNode({},process.execPath),resolve(process.execPath));
  assert.equal(selectTestNode({GENSHIN_NATIVE_NODE:'explicit.exe',PATH:'old-package'},'other.exe'),resolve('explicit.exe'));
});
test('actual running host matches independently read source pins',()=>{
  assert.equal(verifyTestNode(process.execPath,readIndependentNodePins()),resolve(process.execPath));
  assert.equal(pinnedTestNode(),selectTestNode());
});
test('a bad explicit path never falls back to the valid running host',()=>{
  assert.throws(()=>verifyTestNode(selectTestNode({GENSHIN_NATIVE_NODE:join(root,'build/missing-node.exe')}),readIndependentNodePins()));
});
test('wrong SHA is rejected before an unexecutable file could be launched',()=>{
  const folder=mkdtempSync(join(root,'build/r9-test-node-')),node=join(folder,'node.exe');writeFileSync(node,'not an executable');
  assert.throws(()=>verifyTestNode(node,readIndependentNodePins()),{code:'pinned_test_node_sha_mismatch'});
});
test('matching bytes do not excuse a wrong required version',()=>{
  const pins=readIndependentNodePins();assert.throws(()=>verifyTestNode(process.execPath,{...pins,version:'0.0.1'}),{code:'pinned_test_node_version_mismatch'});
});
test('a host alias is not an independent pinned executable',t=>{
  const folder=mkdtempSync(join(root,'build/r9-test-node-')),alias=join(folder,'alias.exe');
  try{symlinkSync(process.execPath,alias,'file');}catch(error){if(['EPERM','EACCES'].includes(error.code)){t.skip('Windows symlink privilege unavailable');return;}throw error;}
  assert.throws(()=>verifyTestNode(alias,readIndependentNodePins()),{code:'pinned_test_node_alias_or_type'});
});
