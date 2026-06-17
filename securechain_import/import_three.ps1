# .\securechain_import\import_three.ps1
# .\securechain_import\import_three.ps1 --dry-run

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

python -m securechain_import.import_three_anchors @args
