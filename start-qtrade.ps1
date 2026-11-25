param(
    [int]$Port = 3028,
    [switch]$SkipInstall,
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
foreach ($command in @('uv', 'npm')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Missing $command. Install Python 3.12, Node.js 20+ and uv; see README.md."
    }
}
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Port must be between 1024 and 65535.' }
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is occupied. Choose another port with -Port. No process was stopped."
}
$env:PYTHONUTF8 = '1'
if (-not $env:DATA_DIR) { $env:DATA_DIR = Join-Path $ProjectRoot 'data' }
Push-Location (Join-Path $ProjectRoot 'backend')
try {
    if (-not $SkipInstall) {
        & uv sync --frozen --system-certs
        if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
    }
} finally { Pop-Location }
Push-Location (Join-Path $ProjectRoot 'frontend')
try {
    if (-not $SkipInstall) {
        & npm exec --yes --package=pnpm@9.10.0 -- pnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    }
    if (-not $SkipBuild) {
        & npm exec --yes --package=pnpm@9.10.0 -- pnpm build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    if (-not (Test-Path -LiteralPath 'dist/index.html')) { throw 'Frontend dist is missing; omit -SkipBuild.' }
} finally { Pop-Location }
Write-Host "Qtrade: http://127.0.0.1:$Port/qtrade" -ForegroundColor Green
Write-Host 'Local simulation only. Press Ctrl+C to stop the service.'
Push-Location (Join-Path $ProjectRoot 'backend')
try { & uv run --frozen uvicorn app.main:app --host 127.0.0.1 --port $Port }
finally { Pop-Location }
