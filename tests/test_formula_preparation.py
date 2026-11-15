import copy
import io
import zipfile

import pytest

from genshin_app import formula_workbook as subject
from genshin_app.equipment import run_plan_calc
from tests.test_resource_lifecycle_app_gates import gated_plan
from tests.test_shared_pool_app_delivery import authorize


@pytest.fixture
def value(tmp_path):
    db = tmp_path / 'absent.db'
    return run_plan_calc(authorize(gated_plan(), db), db)


@pytest.mark.parametrize('entry', ['support', 'build', 'write', 'validate', 'links'])
def test_each_entry_verifies_once_and_next_call_verifies_again(value, tmp_path, monkeypatch, entry):
    real = subject._unpack
    calls = []
    def unpack(calculated, **kwargs):
        calls.append(calculated)
        return real(calculated, **kwargs)
    monkeypatch.setattr(subject, '_unpack', unpack)
    entries = {'support': subject.formula_workbook_support, 'build': subject.build_formula_workbook,
               'write': lambda v: subject.write_formula_workbook(v, tmp_path / 'result.xlsx'),
               'validate': subject.validate_formula_calculation, 'links': subject._mechanics_links}
    before = copy.deepcopy(value)
    entries[entry](value)
    assert len(calls) == 1 and value == before
    entries[entry](value)
    assert len(calls) == 2 and value == before
    assert calls[0] is not value if entry in ('build', 'write') else calls[0] is value


def test_write_bytes_support_and_receipt_come_from_same_snapshot(value, tmp_path, monkeypatch):
    original = copy.deepcopy(value)
    support = subject.formula_workbook_support(original)
    expected = subject.build_formula_workbook(original)
    old_xlsx = subject._xlsx
    def mutate_original(sheets):
        value['result_receipt']['result_fingerprint'] = 'changed-outside-snapshot'
        value['plan']['plan_id'] = 'outside-change'
        return old_xlsx(sheets)
    monkeypatch.setattr(subject, '_xlsx', mutate_original)
    path = tmp_path / 'result.xlsx'
    output = subject.write_formula_workbook(value, path)
    assert output['support'] == support
    assert output['result_receipt'] == original['result_receipt']
    with zipfile.ZipFile(io.BytesIO(expected)) as a, zipfile.ZipFile(path) as b:
        assert a.namelist() == b.namelist()
        assert all(a.read(name) == b.read(name) for name in a.namelist())


@pytest.mark.parametrize('entry', ['support', 'build', 'write', 'validate', 'links'])
def test_prior_success_never_authorizes_changed_material(value, tmp_path, entry):
    subject.formula_workbook_support(value)
    value['result']['summary']['total_damage'] += 1
    entries = {'support': subject.formula_workbook_support, 'build': subject.build_formula_workbook,
               'write': lambda v: subject.write_formula_workbook(v, tmp_path / 'bad.xlsx'),
               'validate': subject.validate_formula_calculation, 'links': subject._mechanics_links}
    with pytest.raises(subject.FormulaWorkbookError):
        entries[entry](value)
    assert not (tmp_path / 'bad.xlsx').exists()
