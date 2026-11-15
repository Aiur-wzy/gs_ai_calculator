param(
    [Parameter(Mandatory = $true)]
    [string] $Node,
    [string] $Python = "python"
)

$ErrorActionPreference = "Stop"
$resolvedNode = (Resolve-Path -LiteralPath $Node).Path
$version = (& $resolvedNode --version).Trim()
if ($LASTEXITCODE -ne 0 -or $version -ne "v24.19.0") {
    throw "Phase 1 smoke requires the pinned Node v24.19.0 runtime."
}

$previousSmoke = $env:GENSHIN_NATIVE_SIDECAR_OS_SMOKE
$previousNode = $env:GENSHIN_NATIVE_NODE
try {
    $env:GENSHIN_NATIVE_SIDECAR_OS_SMOKE = "1"
    $env:GENSHIN_NATIVE_NODE = $resolvedNode
    & $Python -m pytest tests/test_native_phase1_sidecar_os.py -q
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 1 native sidecar OS smoke failed."
    }
}
finally {
    $env:GENSHIN_NATIVE_SIDECAR_OS_SMOKE = $previousSmoke
    $env:GENSHIN_NATIVE_NODE = $previousNode
}
