"""Independent public imports in fresh isolated processes, without preloading."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('module', ['genshin_app.gui.api', 'genshin_app.export',
                                   'genshin_app.formula_workbook', 'genshin_app.equipment.service'])
def test_fresh_process_import_public_entry_does_not_need_prior_equipment_import(module):
    source = str(ROOT / 'src')
    script = f'import sys;sys.path.insert(0,{source!r});import importlib;importlib.import_module({module!r});print("cold_import_pass")'
    process = subprocess.run([sys.executable, '-I', '-c', script], cwd=ROOT,
                             text=True, encoding='utf-8', capture_output=True, timeout=45, check=False)
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == 'cold_import_pass'
