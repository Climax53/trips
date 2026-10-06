# Trips — one conversation with three AI coding tools in the current folder.
# For running from a clone without installing. From any project folder:
#   Trips
#   Trips "describe the change you want"
#   Trips doctor

$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "trips.py"
if (Get-Command python -ErrorAction SilentlyContinue) {
    & python $script @args
} else {
    & py -3 $script @args
}
exit $LASTEXITCODE
