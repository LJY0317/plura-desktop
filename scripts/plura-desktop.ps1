$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
python (Join-Path $root "src\plura_desktop.py") @args
exit $LASTEXITCODE
