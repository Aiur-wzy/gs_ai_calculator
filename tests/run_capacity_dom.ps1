param(
  [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$RunName = ('chrome-' + (Get-Date -Format 'yyyyMMdd-HHmmss')),
  [string]$ChromePath = (Join-Path $env:ProgramFiles 'Google/Chrome/Application/chrome.exe')
)
$ErrorActionPreference = 'Stop'
$candidate = Split-Path -Parent $PSScriptRoot
$evidence = Join-Path $candidate 'ui-dom-evidence'
$profile = Join-Path $candidate ('ui-dom-profile-' + $RunName)
$html = Join-Path $evidence ($RunName + '.html')
$errors = Join-Path $evidence ($RunName + '.stderr.log')
if (Test-Path -LiteralPath $html) { throw 'Use a new RunName to preserve earlier evidence.' }
New-Item -ItemType Directory -Force $evidence | Out-Null
$uri = ([Uri](Join-Path $PSScriptRoot 'gui_capacity_dom.html')).AbsoluteUri
$arguments = @('--headless', '--disable-gpu', '--disable-background-networking', '--disable-sync', '--no-first-run', '--no-default-browser-check', '--no-proxy-server', '--host-resolver-rules="MAP * 0.0.0.0"', ('--user-data-dir="' + $profile + '"'), '--dump-dom', $uri)
$process = Start-Process -FilePath $ChromePath -ArgumentList $arguments -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput $html -RedirectStandardError $errors
if ($process.ExitCode -ne 0) { throw ('Headless browser exit code: ' + $process.ExitCode) }
& python -X utf8 (Join-Path $PSScriptRoot 'read_capacity_dom_evidence.py') $html
if ($LASTEXITCODE -ne 0) { throw 'The real DOM assertions failed. See preserved evidence.' }
