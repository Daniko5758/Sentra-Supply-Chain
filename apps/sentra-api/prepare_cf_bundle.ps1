$ErrorActionPreference = "Stop"

$apiRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $apiRoot "..\.."))
$cfRoot = [System.IO.Path]::GetFullPath((Join-Path $apiRoot ".cf"))
$bundleRoot = [System.IO.Path]::GetFullPath((Join-Path $cfRoot "deploy"))
$expectedBundleRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".cf\deploy"))

if (-not [string]::Equals($bundleRoot, $expectedBundleRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Unexpected Cloud Foundry bundle path."
}

$apiPackage = Join-Path $apiRoot "sentra_api"
$mockRoot = Join-Path $repoRoot "apps\mock-s4"
$mockPackage = Join-Path $mockRoot "mock_s4"
# v1.6 (DEC-035): the app reads data/<SENTRA_SEED_FILE>; every seed a deploy may select ships, the DEMO seed v2.1.1 is the code
# default. 30 Okt: the v3 DEMO seed ships too (CF selects it with SENTRA_SEED_FILE=sentra_v3_demo_seed.json).
$seedFiles = @("sentra_v2_1_1_seed.json", "sentra_v2_2_test_seed.json", "sentra_v3_demo_seed.json") |
    ForEach-Object { Join-Path $repoRoot "data\$_" }
# B-21: only the static build of the UI ships (sentra_api.web serves it); never the web project folder or its sources.
$webDist = Join-Path $repoRoot "apps\sentra-web\dist"

$requiredPaths = @(
    $apiPackage,
    (Join-Path $apiRoot "pyproject.toml"),
    $mockPackage,
    (Join-Path $mockRoot "pyproject.toml"),
    (Join-Path $apiRoot "requirements.txt"),
    (Join-Path $apiRoot "runtime.txt"),
    (Join-Path $apiRoot ".cfignore")
) + $seedFiles

foreach ($requiredPath in $requiredPaths) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required deploy input is missing: $requiredPath"
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $webDist "index.html"))) {
    throw "The UI build is missing: $webDist\index.html. Run 'npm run build' in apps\sentra-web first."
}

if (-not (Test-Path -LiteralPath $cfRoot)) {
    New-Item -ItemType Directory -Path $cfRoot | Out-Null
}

if (Test-Path -LiteralPath $bundleRoot) {
    $resolvedCfRoot = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $cfRoot).Path).TrimEnd('\')
    $resolvedBundleRoot = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $bundleRoot).Path).TrimEnd('\')
    $allowedPrefix = $resolvedCfRoot + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolvedBundleRoot.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to replace a bundle outside $resolvedCfRoot."
    }
    Remove-Item -LiteralPath $resolvedBundleRoot -Recurse -Force
}

$bundleApiRoot = Join-Path $bundleRoot "apps\sentra-api"
$bundleMockRoot = Join-Path $bundleRoot "apps\mock-s4"
$bundleDataRoot = Join-Path $bundleRoot "data"
New-Item -ItemType Directory -Path $bundleApiRoot, $bundleMockRoot, $bundleDataRoot -Force | Out-Null

# Keep this relative layout: mock_s4.seed_data resolves data/<SENTRA_SEED_FILE> from the bundle root.
Copy-Item -LiteralPath $apiPackage -Destination $bundleApiRoot -Recurse
Copy-Item -LiteralPath (Join-Path $apiRoot "pyproject.toml") -Destination $bundleApiRoot
Copy-Item -LiteralPath $mockPackage -Destination $bundleMockRoot -Recurse
Copy-Item -LiteralPath (Join-Path $mockRoot "pyproject.toml") -Destination $bundleMockRoot
foreach ($seedFile in $seedFiles) { Copy-Item -LiteralPath $seedFile -Destination $bundleDataRoot }
# sentra_api.web.default_web_dist() reads <bundle root>/apps/sentra-web/dist, the same layout as the repository.
$bundleWebRoot = Join-Path $bundleRoot "apps\sentra-web"
New-Item -ItemType Directory -Path $bundleWebRoot -Force | Out-Null
Copy-Item -LiteralPath $webDist -Destination $bundleWebRoot -Recurse
Copy-Item -LiteralPath (Join-Path $apiRoot "requirements.txt") -Destination $bundleRoot
Copy-Item -LiteralPath (Join-Path $apiRoot "runtime.txt") -Destination $bundleRoot
Copy-Item -LiteralPath (Join-Path $apiRoot ".cfignore") -Destination $bundleRoot

# Compiled caches of the local machine are not part of the app (.cfignore skips them at push as well).
Get-ChildItem -LiteralPath $bundleRoot -Recurse -Directory -Filter "__pycache__" -Force |
    Sort-Object { $_.FullName.Length } -Descending |
    ForEach-Object { if (Test-Path -LiteralPath $_.FullName) { Remove-Item -LiteralPath $_.FullName -Recurse -Force } }

# The bundle has no .git; record the commit for GET /health (EP-27). Skipped without git.
$buildSha = $null
try {
    $ErrorActionPreference = "Continue"
    $buildSha = (git -C $repoRoot rev-parse HEAD 2>$null | Out-String).Trim()
} catch {
    $buildSha = $null
} finally {
    $ErrorActionPreference = "Stop"
}
if ($buildSha -match '^[0-9a-f]{40,64}$') {
    Set-Content -LiteralPath (Join-Path $bundleRoot "BUILD_SHA") -Value $buildSha -Encoding ascii
} else {
    Write-Warning "No git commit found; /health will report version 'unknown'."
}

$bundleBytes = (Get-ChildItem -LiteralPath $bundleRoot -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("Prepared Cloud Foundry app bundle at {0} ({1:N1} MB before cf compresses it)" -f $bundleRoot, ($bundleBytes / 1MB))
