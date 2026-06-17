# 20/20 Game -- Local Dev Launcher
# Production data run:  .\start-local.ps1
# Offline demo data:    .\start-local.ps1 -Demo     (build_demo_db.py first)
param([switch]$Demo)

$env:FLASK_APP        = "api/app.py"
$env:FLASK_ENV        = "development"
$env:ADMIN_SECRET     = "dev_secret"
$env:SITE_URL         = "http://localhost:5902"
$env:SPORT_MODE       = "NFL"

if ($Demo) {
    # the bundled demo dataset is small -> shorter chains, loose difficulty
    $env:PUZZLE_CHAIN_LENGTH = "10"
    $env:PUZZLE_DIFFICULTY   = "any"
} else {
    if (-not $env:PUZZLE_CHAIN_LENGTH) { $env:PUZZLE_CHAIN_LENGTH = "20" }
    if (-not $env:PUZZLE_DIFFICULTY)   { $env:PUZZLE_DIFFICULTY   = "medium" }
}

$port = 5902
$machine_ip = (
    Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.InterfaceAlias -notmatch 'Loopback|vEthernet|WSL|169\.254' } |
    Select-Object -First 1 -ExpandProperty IPAddress
)

Write-Host ""
Write-Host "20/20 Game API starting$(if ($Demo) {' (DEMO)'})..."
Write-Host ""
Write-Host "  Local:    http://localhost:$port"
if ($machine_ip) {
    Write-Host "  Network:  http://${machine_ip}:$port"
}
Write-Host ""
Write-Host "  Health:   http://localhost:$port/health"
Write-Host "  Vintage:  http://localhost:$port/api/vintage"
Write-Host ""
Write-Host "  Sport mode:   $env:SPORT_MODE"
Write-Host "  Difficulty:   $env:PUZZLE_DIFFICULTY"
Write-Host "  Chain length: $env:PUZZLE_CHAIN_LENGTH"
Write-Host ""
Write-Host "Press Ctrl+C to stop."
Write-Host ""

flask run --host 0.0.0.0 --port $port
