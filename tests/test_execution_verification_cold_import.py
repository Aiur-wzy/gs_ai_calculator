"""Each import starts a fresh isolated interpreter, with no equipment preload."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('statement', [
    'from genshin_app.gui.api import GuiApi',
    'from genshin_app.export import export_result',
    'from genshin_app.formula_workbook import build_formula_workbook',
    'from genshin_app.calculation_review import review_calculation',
    'from genshin_app.equipment.service import run_plan_calc',
    'from genshin_app.equipment import deliver_plan_file',
    'from genshin_app.cli import main',
    'from genshin_app.verification_scope import verification_scope; assert "genshin_app.equipment" not in sys.modules',
    'from genshin_app.export import export_result; from genshin_app.equipment import run_plan_calc',
    'from genshin_app.equipment import run_plan_calc; from genshin_app.export import export_result',
    'from genshin_app.gui.api import GuiApi; from genshin_app.calculation_review import review_calculation',
    'from genshin_app.gui.api import GuiApi; api=GuiApi(root/"data"); assert api.ping()["code"]==0; assert api.get_enums()["code"]==0; assert api.plan_capabilities()["code"]==0',
    'from genshin_app.equipment.verification_scope import verification_scope as old; from genshin_app.verification_scope import verification_scope as new; assert old is new',
])
def test_cold_public_imports(statement):
    code = f'''
import sys
from pathlib import Path
root=Path({json.dumps(str(ROOT))})
sys.path.insert(0,str(root/'src'))
{statement}
'''
    result = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', code],
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
