$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'uv is required. Follow the installation steps in README.md.'
    }
    if (-not (Test-Path -LiteralPath 'web/dist/index.html')) {
        throw 'Build the frontend first: cd web; npm ci; npm run build'
    }
    Write-Host 'Open http://127.0.0.1:8000 in your browser. Ctrl+C stops the server.'
    & uv run --locked uvicorn server:app --host 127.0.0.1 --port 8000
    if ($LASTEXITCODE -ne 0) { throw "Server exited with code $LASTEXITCODE." }
} finally {
    Pop-Location
}
