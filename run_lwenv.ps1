$ErrorActionPreference = "Stop"

$conda = "C:\ProgramData\anaconda3\Scripts\conda.exe"
if (-not (Test-Path $conda)) {
    throw "未找到 conda：$conda"
}

& $conda run -n lwenv python "$PSScriptRoot\paper_archiver.py"
