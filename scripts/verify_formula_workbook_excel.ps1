param([string]$OutputDirectory = (Join-Path $PSScriptRoot '..\outputs\engineering-formula-d20'))
$ErrorActionPreference = 'Stop'
$taskOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
$taskControls = Get-Content -LiteralPath (Join-Path $taskOutput 'controls.json') -Raw | ConvertFrom-Json
$taskResults = [System.Collections.Generic.List[object]]::new()
$taskExcel = $null
$taskBook = $null
function Assert-Number([object]$Actual, [double]$Expected, [string]$Label) {
    if ($Actual -is [string] -or [Math]::Abs([double]$Actual - $Expected) -gt [Math]::Max(0.0000001, [Math]::Abs($Expected) * 0.0000000001)) {
        throw "$Label : $Actual != $Expected"
    }
}
try {
    # Own a separate hidden Excel instance. Do not attach to the user's session.
    $taskExcel = New-Object -ComObject Excel.Application
    $taskExcel.Visible = $false
    $taskExcel.DisplayAlerts = $false
    foreach ($taskCase in @(
        @{ Name = 'classic'; File = 'formula-probe.xlsx'; Controls = $taskControls; Structure = 'hits[0].count' },
        @{ Name = 'mechanics'; File = 'mechanics-formula-probe.xlsx'; Controls = $taskControls.mechanics; Structure = 'mechanics_model.rules[2].timing.duration' },
        @{ Name = 'energy_window'; File = 'energy-window-formula-probe.xlsx'; Controls = $taskControls.energy_window; Structure = 'rotation_model.energy_recharge_windows[0].end' }
    )) {
        $taskBook = $taskExcel.Workbooks.Open((Join-Path $taskOutput $taskCase.File), 0, $false)
        $taskInput = $taskBook.Worksheets.Item('参数')
        $taskSummary = $taskBook.Worksheets.Item('汇总')
        $taskPaths = @{}
        for ($taskRow = 5; $taskRow -le $taskInput.UsedRange.Rows.Count + 1; $taskRow++) {
            $taskKey = $taskInput.Cells.Item($taskRow, 1).Value2
            if ($null -ne $taskKey) { $taskPaths[[string]$taskKey] = $taskRow }
        }
        $taskExcel.CalculateFullRebuild()
        Assert-Number $taskSummary.Range('B6').Value2 $taskCase.Controls.baseline ($taskCase.Name + ' baseline')
        foreach ($taskEdit in $taskCase.Controls.cases) {
            $taskCell = $taskInput.Cells.Item($taskPaths[$taskEdit.path], 3)
            $taskOriginal = $taskCell.Value2
            $taskCell.Value2 = [double]$taskEdit.value
            $taskExcel.CalculateFullRebuild()
            $taskActual = $taskSummary.Range('B6').Value2
            Assert-Number $taskActual $taskEdit.expected $taskEdit.path
            if ($taskSummary.Range('B9').Value2 -notlike '*失效*') { throw '循环结论未失效' }
            $taskResults.Add(@{ workbook = $taskCase.Name; path = $taskEdit.path; expected = $taskEdit.expected; actual = $taskActual })
            $taskCell.Value2 = $taskOriginal
            $taskExcel.CalculateFullRebuild()
            Assert-Number $taskSummary.Range('B6').Value2 $taskCase.Controls.baseline ($taskCase.Name + ' restored')
        }
        $taskCell = $taskInput.Cells.Item($taskPaths[$taskCase.Structure], 3)
        $taskOriginal = $taskCell.Value2
        $taskBlocked = $false
        try { $taskCell.Value2 = [double]$taskOriginal + 1 } catch { $taskBlocked = $true }
        if (-not $taskBlocked) {
            $taskExcel.CalculateFullRebuild()
            if ($taskSummary.Range('B6').Value2 -ne '需重新生成') { throw '结构变更后仍沿用旧结果' }
        }
        $taskResults.Add(@{ workbook = $taskCase.Name; path = $taskCase.Structure; actual = $(if ($taskBlocked) {'固定结构单元格受保护'} else {'需重新生成'}) })
        $taskBook.Close($false)
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($taskBook)
        $taskBook = $null
    }
    @{ engine = 'Microsoft Excel'; version = $taskExcel.Version; build = $taskExcel.Build; cases = $taskResults; status = 'passed' } |
        ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $taskOutput 'native-excel-acceptance.json') -Encoding utf8
    Write-Output "Native Excel passed $($taskResults.Count) perturbation/structure cases."
}
finally {
    if ($null -ne $taskBook) { $taskBook.Close($false); [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($taskBook) }
    if ($null -ne $taskExcel) { $taskExcel.Quit(); [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($taskExcel) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
