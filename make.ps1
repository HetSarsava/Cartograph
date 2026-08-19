# Cartograph dev tasks for PowerShell (mirrors the Makefile 1:1 for
# environments without GNU make). Usage:
#   .\make.ps1 test
#   .\make.ps1 run
#   .\make.ps1 run -Repo tests/fixtures/sample_repo -Task "convert unittest tests to pytest"

param(
    [Parameter(Position = 0)]
    [string]$Target = "help",

    [string]$Task = "convert unittest tests to pytest",
    [string]$Repo = "tests/fixtures/sample_repo"
)

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv is not on PATH for this session." -ForegroundColor Yellow
    Write-Host 'Run once per new terminal: $env:Path += ";C:\Users\Het Sarsava\AppData\Roaming\Python\Python312\Scripts"'
    Write-Host "Or add it permanently (new terminal required after):"
    Write-Host '  [Environment]::SetEnvironmentVariable("Path", $env:Path + ";C:\Users\Het Sarsava\AppData\Roaming\Python\Python312\Scripts", "User")'
    exit 1
}

function Invoke-Setup { uv sync }
function Invoke-Dev { uv sync --extra dev }
function Invoke-Test { uv run pytest -v }
function Invoke-TestFast { uv run pytest -q }
function Invoke-TestCov { uv run pytest --cov=cartograph --cov-report=term-missing }
function Invoke-Lint { uv run ruff check . }
function Invoke-Format { uv run ruff format . }
function Invoke-Typecheck { uv run mypy src/cartograph }

function Invoke-Check {
    Invoke-Lint
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Invoke-Typecheck
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Invoke-TestFast
}

function Invoke-Demo { uv run cartograph demo }
function Invoke-Models { uv run cartograph models }
function Invoke-Run { uv run cartograph run $Repo $Task }
function Invoke-Serve { uv run cartograph serve }
function Invoke-Tree { uv run cartograph tree $Repo }
function Invoke-Mcp { uv run python -m cartograph.mcp_server.server }

function Invoke-Clean {
    Get-ChildItem -Path . -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notlike "*\.venv\*" } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .pytest_cache, .ruff_cache, .mypy_cache
}

function Invoke-CleanLogs {
    Remove-Item -Force -ErrorAction SilentlyContinue logs\*.log
}

function Show-Help {
    Write-Host "Cartograph dev tasks (PowerShell):"
    Write-Host "  .\make.ps1 setup       uv sync (install runtime deps into .venv)"
    Write-Host "  .\make.ps1 dev         uv sync --extra dev (adds ruff/mypy/pytest-asyncio)"
    Write-Host "  .\make.ps1 test        run the full pytest suite, verbose"
    Write-Host "  .\make.ps1 test-fast   run the full pytest suite, quiet"
    Write-Host "  .\make.ps1 test-cov    run pytest with coverage (requires 'dev')"
    Write-Host "  .\make.ps1 lint        ruff check ."
    Write-Host "  .\make.ps1 format      ruff format ."
    Write-Host "  .\make.ps1 typecheck   mypy src/cartograph"
    Write-Host "  .\make.ps1 check       lint + typecheck + test-fast"
    Write-Host "  .\make.ps1 demo        full pipeline on a throwaway copy of the sample repo"
    Write-Host "  .\make.ps1 models      list Groq model ids your API key can use"
    Write-Host "  .\make.ps1 run         cartograph run <Repo> <Task>"
    Write-Host "                         override: .\make.ps1 run -Repo ... -Task '...'"
    Write-Host "  .\make.ps1 serve       start the local web UI (http://127.0.0.1:8765)"
    Write-Host "  .\make.ps1 tree        build a standalone dependency tree for <Repo>"
    Write-Host "  .\make.ps1 mcp         start the MCP server (stdio transport)"
    Write-Host "  .\make.ps1 clean       remove __pycache__/.pytest_cache/.ruff_cache/.mypy_cache"
    Write-Host "  .\make.ps1 clean-logs  remove logs\*.log"
}

switch ($Target) {
    "setup"      { Invoke-Setup }
    "dev"        { Invoke-Dev }
    "test"       { Invoke-Test }
    "test-fast"  { Invoke-TestFast }
    "test-cov"   { Invoke-TestCov }
    "lint"       { Invoke-Lint }
    "format"     { Invoke-Format }
    "typecheck"  { Invoke-Typecheck }
    "check"      { Invoke-Check }
    "demo"       { Invoke-Demo }
    "models"     { Invoke-Models }
    "run"        { Invoke-Run }
    "serve"      { Invoke-Serve }
    "tree"       { Invoke-Tree }
    "mcp"        { Invoke-Mcp }
    "clean"      { Invoke-Clean }
    "clean-logs" { Invoke-CleanLogs }
    "help"       { Show-Help }
    default {
        Write-Host "Unknown target: $Target" -ForegroundColor Red
        Show-Help
        exit 1
    }
}
