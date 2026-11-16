<#
.SYNOPSIS
Explicit, resumable QA-only build and frozen installation qualification.
.DESCRIPTION
Validate is read-only. Other phases require a separately exported, reviewed source
snapshot at RunRoot/source. This is not a public release or a source exporter.
The only signer is the disposable key created by installation_qa_fixture.py.
No phase publishes, reads a production private key, launches GUI, or registers an
installation. Use the separate Windows QA registration/UI procedure afterwards.
.EXAMPLE
pwsh -NoProfile -File scripts/run_installation_qa.ps1 -RunRoot "$env:TEMP\genshin-r21-qa-final-20261116" -Phase Validate
#>
[CmdletBinding()]
param(
    [ValidateSet('Validate', 'Configure', 'BuildSetup', 'BuildApp', 'QualifyPortable', 'Package',
                 'QualifyInstall', 'QualifyUpgrade', 'QualifyLifecycle')]
    [string]$Phase = 'Validate',
    [Parameter(Mandatory = $true)][string]$RunRoot,
    [string]$Python = 'python',
    [string]$OpenSsl = 'C:\Program Files\Git\usr\bin\openssl.exe',
    [string]$NativeNode,
    [switch]$KeepVerifiedSandbox
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$run = [IO.Path]::GetFullPath($RunRoot).TrimEnd('\')
if ([IO.Path]::GetDirectoryName($run) -ne $tempRoot -or
    [IO.Path]::GetFileName($run) -notmatch '^genshin-r21-qa-[A-Za-z0-9_-]+$') {
    throw 'RunRoot must be a direct TEMP child named genshin-r21-qa-<run>.'
}
$source = Join-Path $run 'source'
$qa = Join-Path $run 'qa'
$reports = Join-Path $qa 'reports'
$published = Get-Content -LiteralPath (Join-Path $repo 'build/publication-signing/r20-candidate/candidate-artifacts.json') -Raw | ConvertFrom-Json
$oldSource = $published.source
$oldIndex = Join-Path $repo 'build/publication-signing/r20-candidate/r20-index.signed.json'
$publicKey = Join-Path $repo 'build/publication-signing/production-public-key.json'
$oldApp = Join-Path $oldSource 'dist/candidate/feed/app-0.46.0-r20.app.zip'
$dbZip = Join-Path $oldSource 'dist/candidate/feed/db-7.2.50-r6.sqlite.gz'
$db = Join-Path $oldSource 'data/genshin.db'
$dbManifest = Join-Path $oldSource 'dist/candidate/GenshinCalc/_internal/bundled/database-release-manifest.json'
$oldPair = Join-Path $oldSource 'dist/candidate/GenshinCalc/_internal/bundled/release-pair-manifest.json'
if (-not $NativeNode) {
    $NativeNode = Join-Path $oldSource 'dist/candidate/GenshinCalc/_internal/agent-runtime/node.exe'
}
$setup = Join-Path $source 'dist/qa-setup/GenshinCalcSetup.exe'
$builtApp = Join-Path $source 'dist/qa/GenshinCalc/app'
$updater = Join-Path $source 'dist/qa/GenshinCalcUpdater/GenshinCalcUpdater.exe'
$nodeLicense = Join-Path $source 'third_party/reviewed-licenses/texts/node-v24.19.0-LICENSE.txt'
$licenseEvidence = Join-Path $source 'third_party/reviewed-licenses/manifest.json'
$accepted = Join-Path $source 'data/accepted-r20'
$sourceProof = Join-Path $source 'SOURCE-SNAPSHOT.json'

function Assert-PlainPath([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Linked path is not a QA directory: $cursor"
            }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}
function Hash([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Assert-File([string]$Path, [string]$Expected = '') {
    Assert-PlainPath $Path
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Missing input: $Path" }
    if ($Expected -and (Hash $Path) -ne $Expected) { throw "Input SHA mismatch: $Path" }
}
function Write-Json([string]$Path, $Value) {
    $Value | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $Path -Encoding utf8
}
function Run([string]$Executable, [string[]]$Arguments, [string]$Log) {
    & $Executable @Arguments 2>&1 | Tee-Object -FilePath $Log
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE); see $Log" }
}
function Copy-Exact([string]$From, [string]$To) {
    Assert-PlainPath $To
    if (Test-Path -LiteralPath $To) {
        Assert-File $To (Hash $From)
        return
    }
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($To)) | Out-Null
    Copy-Item -LiteralPath $From -Destination $To
    Assert-File $To (Hash $From)
}
function Assert-Source([bool]$OverlayApplied) {
    Assert-File $sourceProof
    $snapshot = Get-Content -LiteralPath $sourceProof -Raw | ConvertFrom-Json
    if ($snapshot.contract -ne 'genshin-public-source-snapshot-v1' -or $snapshot.status -ne 'copied') {
        throw 'Prepare and verify the independent reviewed source snapshot first.'
    }
    $listed = @{}
    foreach ($row in $snapshot.files) {
        if ($row.path -match '(^/|\\|:|(^|/)\.\.(/|$))') { throw 'Unsafe source snapshot member.' }
        $listed[$row.path] = $true
        $file = Join-Path $source $row.path
        if ($OverlayApplied -and $row.path -eq 'src/genshin_app/version.py') {
            Assert-File $file (Hash (Join-Path $qa 'qa-version.py'))
        } else { Assert-File $file $row.sha256 }
    }
    foreach ($required in @('src/genshin_app/managed_installation.py',
            'src/genshin_app/installation_runtime.py', 'src/genshin_app/windows_product_registration.py',
            'src/genshin_app/setup_frozen_qualification.py', 'app_entry.py', 'installer_entry.py',
            'scripts/build_dist.py', 'scripts/build_release_pair_feed.py', 'scripts/installation_qa_fixture.py',
            'scripts/verify_frozen_setup_archive.py', 'src/genshin_app/gui/web/agent-window.html',
            'src/genshin_app/gui/web/json-transport.js')) {
        if (-not $listed.ContainsKey($required)) { throw "Current source missing from export proof: $required" }
    }
    # Dependency closures and final web/runtime manifests are revalidated by the
    # production builder. Never borrow node_modules through a directory link.
    foreach ($name in @('agent-runtime/node_modules', 'frontend/node_modules')) {
        $dependency = Join-Path $source $name
        Assert-PlainPath $dependency
        if (-not (Test-Path -LiteralPath $dependency -PathType Container)) {
            throw "Offline dependency closure not prepared: $name"
        }
    }
}

Assert-PlainPath $run
Assert-File $oldApp '8fceaac7772545a6170cb7fda9f1cf83867dc3a6db8c92a56115a09e66057315'
Assert-File $dbZip '5b4dd160416a0c7622fbf64698646e8fd2dba44f7c27fda06be74e167ff71283'
Assert-File $db '27e6e2a85b8f11e0c646fc6ead9aa40671e5ceedcecae17b479f5e8017f13dca'
Assert-File $oldIndex $published.signed_index_sha256
foreach ($path in @($publicKey, $dbManifest, $oldPair, $NativeNode, $OpenSsl)) { Assert-File $path }
Assert-File $NativeNode '3602f2bb1a10f2cbab4c36886218a33c1ab3db87290e73b033c46c77147d0237'
$oldIndexValue = Get-Content -LiteralPath $oldIndex -Raw | ConvertFrom-Json
$sequence = 1 + [int](($oldIndexValue.pairs | ForEach-Object { $_.pair_manifest.pair_sequence } | Measure-Object -Maximum).Maximum)
$drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($run))
$plan = [ordered]@{
    contract = 'genshin-installation-qa-runbook-v1'; production_authority = $false
    phase = $Phase; run_root = $run; source = $source; qa_root = $qa
    source_snapshot_present = (Test-Path -LiteralPath $sourceProof -PathType Leaf)
    required_source_preparation = 'reviewed independent current-source export + offline dependencies + final coherent frontend/runtime'
    setup = $setup; built_app = $builtApp; updater = $updater
    original_r20_app = $oldApp; original_r6_gzip = $dbZip; original_r6_database = $db
    previous_index = $oldIndex; public_verification_key = $publicKey
    node = $NativeNode; pair_sequence = $sequence; free_bytes = $drive.AvailableFreeSpace
    suggested_initial_free_bytes = 16GB; acceptance_free_bytes = 6GB
    order = @('Configure', 'BuildSetup', 'BuildApp (App then Updater)',
              'QualifyPortable (bare default data path; no explicit data directory)',
              'Package (embed Setup and Updater before inventory/signing)',
              'QualifyInstall', 'QualifyUpgrade', 'QualifyLifecycle',
              'separate explicit Windows QA registration and manual GUI acceptance')
}
if ($Phase -eq 'Validate') { $plan | ConvertTo-Json -Depth 8; exit 0 }
if ($drive.AvailableFreeSpace -lt 6GB) { throw 'Less than 6 GiB on the QA volume; free QA-owned space before continuing.' }
Assert-Source ($Phase -ne 'Configure')
$fixtureScript = Join-Path $source 'scripts/installation_qa_fixture.py'
if ($Phase -eq 'Configure') {
    if (Test-Path -LiteralPath $qa) { throw 'QA root exists. Resume a later phase; do not overwrite its signer.' }
    # Only fresh QA key generation; no option accepts a production signer.
    & $Python $fixtureScript init --root $qa --openssl $OpenSsl --production-public-key $publicKey
    if ($LASTEXITCODE -ne 0) { throw 'QA trust initialization failed.' }
    [IO.Directory]::CreateDirectory($reports) | Out-Null
    & $Python $fixtureScript version-overlay --root $qa --source (Join-Path $source 'src/genshin_app/version.py') --database-manifest $dbManifest --predecessor-pair $oldPair --app-release-id app-0.46.0-r21-qa --pair-id pair-0.46.0-r21-qa-db-7.2.50-r6 --pair-sequence $sequence
    if ($LASTEXITCODE -ne 0) { throw 'QA overlay generation failed.' }
    Copy-Item -LiteralPath (Join-Path $qa 'qa-version.py') -Destination (Join-Path $source 'src/genshin_app/version.py')
    Copy-Exact $db (Join-Path $source 'data/genshin.db')
    Copy-Exact $dbManifest (Join-Path $accepted 'database-release-manifest.json')
    Copy-Exact $oldPair (Join-Path $accepted 'release-pair-manifest.json')
    Write-Json (Join-Path $reports 'inputs.json') $plan
    exit 0
}
Assert-File (Join-Path $qa 'QA_ONLY.json')
[IO.Directory]::CreateDirectory($reports) | Out-Null
$oldPythonPath = $env:PYTHONPATH
$oldInstallerCache = $env:PYINSTALLER_CONFIG_DIR
try {
    # Do not inherit editable-source PYTHONPATH from the developer checkout.
    $env:PYTHONPATH = (Join-Path $source 'src') + [IO.Path]::PathSeparator + $source
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $run 'pyinstaller-cache'
    Push-Location -LiteralPath $source
    try {
        if ($Phase -eq 'BuildSetup') {
            Run $Python @('-m', 'PyInstaller', 'genshin_installer.spec', '--clean', '--noconfirm',
                '--distpath', 'dist/qa-setup', '--workpath', 'build-py/setup') (Join-Path $reports 'build-setup.log')
            Run $Python @('scripts/verify_frozen_setup_archive.py', '--exe', $setup, '--project-root', $source) (Join-Path $reports 'setup-archive.json')
        } elseif ($Phase -eq 'BuildApp') {
            Assert-File $setup
            Assert-File $licenseEvidence
            Assert-File $nodeLicense
            Run $Python @('scripts/build_dist.py', '--dist-root', 'dist/qa', '--database', 'data/genshin.db',
                '--accepted-database-manifest', 'data/accepted-r20/database-release-manifest.json',
                '--accepted-predecessor-pair-manifest', 'data/accepted-r20/release-pair-manifest.json',
                '--native-node', $NativeNode, '--node-license', $nodeLicense, '--license-evidence', $licenseEvidence,
                '--released-at', [DateTimeOffset]::Now.ToString('o')) (Join-Path $reports 'build-app-updater.log')
        } elseif ($Phase -eq 'QualifyPortable') {
            Run $Python @('scripts/verify_portable_startup.py', '--app-root', $builtApp,
                '--report', (Join-Path $reports 'portable-default.json')) (Join-Path $reports 'portable-default-stdout.log')
        } elseif ($Phase -eq 'Package') {
            $supply = Get-Content -LiteralPath (Join-Path $builtApp 'supply-chain-receipt.json') -Raw | ConvertFrom-Json
            if (-not $supply.machine_complete -or $supply.review_required_count -ne 0) {
                throw 'Actual build dependency license inventory is incomplete; resolve current items before packaging.'
            }
            Run $Python @('scripts/build_release_pair_feed.py', 'prepare-app', '--source', $builtApp,
                '--updater', $updater, '--maintenance', $setup, '--output', (Join-Path $qa 'independent-app')) (Join-Path $reports 'prepare-app.json')
            Run $Python @($fixtureScript, 'prepare-feed', '--root', $qa, '--app-root', (Join-Path $qa 'independent-app'),
                '--database', $db, '--database-manifest', $dbManifest, '--previous-index', $oldIndex,
                '--database-archive', $dbZip) (Join-Path $reports 'prepare-feed.json')
            Run $Python @($fixtureScript, 'sign-feed', '--root', $qa, '--feed', (Join-Path $qa 'feed'),
                '--previous-index', $oldIndex, '--openssl', $OpenSsl) (Join-Path $reports 'sign-feed.json')
        } else {
            $inputs = Get-Content -LiteralPath (Join-Path $qa 'fixture.json') -Raw | ConvertFrom-Json
            $sandbox = Join-Path $tempRoot ('genshin-setup-qualification-' + [guid]::NewGuid().ToString('N'))
            $report = Join-Path $sandbox 'report.json'
            $mode = @{ QualifyInstall = 'install'; QualifyUpgrade = 'upgrade'; QualifyLifecycle = 'lifecycle' }[$Phase]
            $parameters = @("--verify-frozen-$mode", '--index', $inputs.index, '--app-archive', $inputs.app_archive,
                '--database-archive', $dbZip, '--sandbox-root', $sandbox, '--report', $report)
            if ($Phase -eq 'QualifyUpgrade') {
                $parameters += @('--previous-index', $oldIndex, '--previous-app-archive', $oldApp)
            }
            Write-Json (Join-Path $reports "$mode-started.json") @{sandbox = $sandbox; inputs = $inputs; production_authority = $false}
            Run $setup $parameters (Join-Path $reports "$mode-stdout.log")
            $result = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
            if ($result.status -ne 'verified') { throw "Frozen $mode did not verify; preserve $sandbox" }
            Copy-Exact $report (Join-Path $reports "$mode-report.json")
            if (-not $KeepVerifiedSandbox) {
                # Only this invocation's new UUID directory after success. Never
                # enumerate names and hand deletion to a second shell.
                Assert-PlainPath $sandbox
                if ([IO.Path]::GetDirectoryName($sandbox) -ne $tempRoot -or
                    [IO.Path]::GetFileName($sandbox) -notmatch '^genshin-setup-qualification-[0-9a-f]{32}$') {
                    throw 'Cleanup target is not the owned QA sandbox.'
                }
                $linked = Get-ChildItem -LiteralPath $sandbox -Recurse -Force |
                    Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } | Select-Object -First 1
                if ($linked) { throw 'Preserving QA sandbox containing a reparse point.' }
                Remove-Item -LiteralPath $sandbox -Recurse -Force
            }
        }
    } finally { Pop-Location }
} finally {
    $env:PYTHONPATH = $oldPythonPath
    $env:PYINSTALLER_CONFIG_DIR = $oldInstallerCache
}
