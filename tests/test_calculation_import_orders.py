"""A genuinely fresh GUI factory must not depend on prior equipment imports."""
from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize('imports', [
    ['genshin_app.gui.api','genshin_app.equipment.service','genshin_app.export'],
    ['genshin_app.export','genshin_app.gui.api','genshin_app.calculation_integrity'],
    ['genshin_app.calculation_integrity','genshin_app.gui.api','genshin_app.equipment.service'],
])
def test_fresh_factory_import_order(imports):
    source = '\n'.join('import '+name for name in imports) + '\nprint("imports_ready")'
    result = subprocess.run([sys.executable,'-c',source], capture_output=True,text=True,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'imports_ready'
