"""New saved-result proof targets; private v2 is not yet export-authorized."""
import copy

import pytest

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.service import result_bytes
from tests.test_counter_self_frame_app import self_plan
from tests.test_shared_pool_app_delivery import authorize


@pytest.mark.parametrize('tamper', ['strip_history', 'capture_context'])
def test_rehash_does_not_authorize_changed_counter_frame_result(tmp_path, tamper):
    db = tmp_path / 'absent.db'
    out = run_plan_calc(authorize(self_plan(), db), db)
    result, receipt = copy.deepcopy(out['result']), copy.deepcopy(out['result_receipt'])
    if tamper == 'strip_history':
        result['resource_counter_views'].pop('historical_capture')
    else:
        result['resource_counter_views']['trace'][0]['capture_context']['event_id'] = 'not-the-capture'
    receipt['result_fingerprint'] = fingerprint(result)
    with pytest.raises(ValueError):
        result_bytes(result, receipt, 'json')
