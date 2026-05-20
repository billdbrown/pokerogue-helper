# Build Pokerogue Helper as a Windows application bundle.
# Output: dist\PokerogueHelper_vXX\PokerogueHelper_vXX.exe  (plus supporting files)
#
# Tesseract OCR is auto-detected on this machine and copied into the bundle
# so the end user does NOT need a separate Tesseract installation.
#
# Usage:
#   .\build.ps1           — build (incremental)
#   .\build.ps1 -Clean    — delete build\ and dist\ first, then build

param([switch]$Clean)

Set-Location $PSScriptRoot

# ── Derive versioned app name from version.txt ────────────────────────────────
$ver     = (Get-Content "version.txt" -Raw).Trim().Replace(".", "")
$appName = "PokerogueHelper_v$ver"
Write-Host "Version: $((Get-Content 'version.txt' -Raw).Trim())  →  $appName"

# ── Helper: locate Tesseract install dir on this machine ─────────────────────
function Find-TesseractDir {
    foreach ($hive in @("HKLM:\SOFTWARE\Tesseract-OCR", "HKCU:\SOFTWARE\Tesseract-OCR")) {
        try {
            $dir = (Get-ItemProperty -Path $hive -Name InstallDir -ErrorAction Stop).InstallDir
            if (Test-Path "$dir\tesseract.exe") { return $dir }
        } catch {}
    }
    foreach ($p in @(
        "C:\Program Files\Tesseract-OCR",
        "C:\Program Files (x86)\Tesseract-OCR",
        "C:\Users\Public\Tesseract-OCR"
    )) {
        if (Test-Path "$p\tesseract.exe") { return $p }
    }
    $found = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($found) { return Split-Path $found.Source }
    return $null
}

# ── Clean ─────────────────────────────────────────────────────────────────────
if ($Clean) {
    Write-Host "Cleaning previous build..."
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
}

# ── PyInstaller ───────────────────────────────────────────────────────────────
Write-Host "Building $appName..."
python -m PyInstaller pokerogue_helper.spec --noconfirm

if ($LASTEXITCODE -ne 0) {
    Write-Host "`nBuild FAILED (exit $LASTEXITCODE)." -ForegroundColor Red
    exit $LASTEXITCODE
}

# ── Bundle Tesseract ──────────────────────────────────────────────────────────
$tDir = Find-TesseractDir
if (-not $tDir) {
    Write-Host ""
    Write-Host "WARNING: Tesseract not found on this machine." -ForegroundColor Yellow
    Write-Host "         The bundle will require Tesseract to be installed by the end user."
    Write-Host "         Download: https://github.com/UB-Mannheim/tesseract/wiki"
} else {
    Write-Host ""
    Write-Host "Bundling Tesseract from: $tDir"

    $dest = "dist\$appName\tesseract"
    New-Item -ItemType Directory -Force $dest | Out-Null

    Copy-Item "$tDir\tesseract.exe" $dest
    Get-ChildItem "$tDir\*.dll" | Copy-Item -Destination $dest

    $tessdata = "$dest\tessdata"
    New-Item -ItemType Directory -Force $tessdata | Out-Null
    if (Test-Path "$tDir\tessdata\eng.traineddata") {
        Copy-Item "$tDir\tessdata\eng.traineddata" $tessdata
        Write-Host "  Copied eng.traineddata"
    } else {
        Write-Host "  WARNING: eng.traineddata not found in $tDir\tessdata\" -ForegroundColor Yellow
    }
    Write-Host "  Done bundling Tesseract."
}

# ── Trim unused Qt directories ────────────────────────────────────────────────
Write-Host ""
Write-Host "Trimming unused Qt assets..."
$qt6 = "dist\$appName\_internal\PyQt6\Qt6"
foreach ($dir in @("qml", "qsci")) {
    $p = "$qt6\$dir"
    if (Test-Path $p) {
        Remove-Item -Recurse -Force $p
        Write-Host "  Removed $dir\"
    }
}
$trans = "$qt6\translations"
if (Test-Path $trans) {
    $n = (Get-ChildItem "$trans\*.qm").Count
    Get-ChildItem "$trans\*.qm" | Remove-Item -Force
    Write-Host "  Removed $n Qt UI translation files (.qm)"
}
$locales = "$qt6\translations\qtwebengine_locales"
if (Test-Path $locales) {
    $keep = @("en-US.pak", "en-GB.pak")
    $n = (Get-ChildItem "$locales\*.pak" | Where-Object { $_.Name -notin $keep }).Count
    Get-ChildItem "$locales\*.pak" | Where-Object { $_.Name -notin $keep } | Remove-Item -Force
    Write-Host "  Removed $n non-English WebEngine locale files"
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Build succeeded." -ForegroundColor Green
Write-Host "Output folder: $PSScriptRoot\dist\$appName\"
Write-Host "Executable:    dist\$appName\$appName.exe"

$size = (Get-ChildItem "dist\$appName" -Recurse | Measure-Object -Property Length -Sum).Sum
Write-Host ("Bundle size:   {0:N0} MB" -f ($size / 1MB))
