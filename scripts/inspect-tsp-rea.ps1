param(
    [Parameter(Mandatory=$true)][string]$TargetPath,
    [string]$EvidenceDirectory = 'rea-evidence',
    [switch]$JavaScript
)
$ErrorActionPreference = 'Stop'
$SelectedTarget = (Resolve-Path -LiteralPath $TargetPath).Path
$EvidenceRoot = [IO.Path]::GetFullPath($EvidenceDirectory)
New-Item -ItemType Directory -Force -Path $EvidenceRoot | Out-Null
if ($JavaScript) {
    # Select only the installed static directory, never the user data directory.
    & npm exec --yes --package=node@24 --package=rea-agents@6.10.1 -- rea analyze-javascript-application $SelectedTarget --artifact-format directory --json |
        Out-File -LiteralPath (Join-Path $EvidenceRoot 'javascript.json') -Encoding utf8
} else {
    & npm exec --yes --package=node@24 --package=rea-agents@6.10.1 -- rea inspect-artifact $SelectedTarget --detail summary --evidence_output (Join-Path $EvidenceRoot 'artifact.json') --json
}
if ($LASTEXITCODE -ne 0) { throw 'REA inspection failed; do not treat partial output as complete evidence.' }
