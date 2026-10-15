param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$axisInstallRoot = $PSScriptRoot
$axisVenvPython = Join-Path $axisInstallRoot '.venv\Scripts\python.exe'

function Test-AxisPython([string]$Executable) {
    if (-not $Executable) { return $false }
    try {
        & $Executable -c 'import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] < (3,13) and sys.maxsize > 2**32 else 1)' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

if (Test-Path -LiteralPath $axisVenvPython) {
    if (-not (Test-AxisPython $axisVenvPython)) {
        throw 'The existing .venv needs 64-bit Python 3.11 or 3.12. Rename .venv, then run install.ps1 again.'
    }
} else {
    $axisRuntime = $null
    if ($Python) {
        if (-not (Test-AxisPython $Python)) { throw 'The supplied Python must be 64-bit Python 3.11 or 3.12.' }
        $axisRuntime = $Python
    }
    if (-not $axisRuntime -and (Get-Command py -ErrorAction SilentlyContinue)) {
        foreach ($axisVersion in @('3.12','3.11')) {
            try {
                $axisResult = & py "-$axisVersion" -c 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0 -and (Test-AxisPython ([string]$axisResult))) {
                    $axisRuntime = [string]$axisResult
                    break
                }
            } catch { }
        }
    }
    if (-not $axisRuntime) {
        foreach ($axisName in @('python','python3')) {
            $axisCommand = Get-Command $axisName -CommandType Application -ErrorAction SilentlyContinue
            if ($axisCommand -and (Test-AxisPython $axisCommand.Source)) {
                $axisRuntime = $axisCommand.Source
                break
            }
        }
    }
    if (-not $axisRuntime) { throw 'Install 64-bit Python 3.12 from python.org, then retry. Python 3.13+ is not supported by RapidOCR 1.4.4.' }
    & $axisRuntime -m venv (Join-Path $axisInstallRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}

& $axisVenvPython -X utf8 -m pip install -r (Join-Path $axisInstallRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the network connection, then run this script again.' }
& $axisVenvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency validation failed.' }
Write-Host 'Installation complete. Run start.cmd. Download the optional Character Catalog from the app when needed.'
