"""The real Node boundary accepts truthful storage failure, rejects fake IDs."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_node_system_failure_storage_contract():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node runtime is not installed")
    module = (Path(__file__).resolve().parents[1] / "agent-runtime/src/native-app-system-failure.mjs").as_uri()
    script = """
import assert from 'node:assert/strict';
const {validateAppSystemFailure} = await import(MODULE);
const value = {code:1,reason:'app_internal_error',message:'故障',failure_kind:'system',retryable:false,
 system_fault:{contract:'genshin-app-system-failure-v1',component:'app_domain_tools',cause_code:'internal_exception',
 operation:'gs_query_runtime',diagnostic_id:null}, recovery:{action:'report_system_fault',retry_same_request:false},
 diagnostic_storage:{state:'failed',reason:'diagnostic_persistence_failed'}};
assert.equal(validateAppSystemFailure(value),true);
assert.throws(()=>validateAppSystemFailure({...value,diagnostic_storage:{state:'persisted'}}),/invalid_app_system_failure/);
assert.throws(()=>validateAppSystemFailure({...value,system_fault:{...value.system_fault,
 diagnostic_id:'a47a2090-670e-41ae-80a5-76dbd65cfe61'}}),/invalid_app_system_failure/);
console.log('ok');
""".replace("MODULE", json.dumps(module))
    result = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
