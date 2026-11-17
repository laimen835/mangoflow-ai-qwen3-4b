$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install requirements-local.txt first.' }
if (!(Test-Path -LiteralPath '.env.local')) { throw 'Copy .env.example to .env.local and configure local services first.' }
& $taskPython -m uvicorn src.core.main:app --host 127.0.0.1 --port 8000 --no-access-log
exit $LASTEXITCODE
