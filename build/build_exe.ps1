param(
    [switch]$OneFile = $false,
    [switch]$Clean = $true
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$PyInstaller = Join-Path $ProjectRoot ".venv\Scripts\pyinstaller.exe"
$SpecFile = Join-Path $ScriptDir "atro.spec"
$WorkDir = Join-Path $ScriptDir "work"
$DistDir = Join-Path $ProjectRoot "dist"
$OnedirTarget = Join-Path $DistDir "ATRO"
$StageDist = Join-Path $WorkDir "_stage_dist"

if (-not (Test-Path $PyInstaller)) {
    throw "PyInstaller tidak ditemukan di '$PyInstaller'. Pastikan .venv sudah terinstall."
}

$running = Get-Process -Name "ATRO" -ErrorAction SilentlyContinue
if ($running) {
    throw "ATRO.exe sedang berjalan (PID: $($running.Id -join ', ')). Tutup jendela ATRO setelah job selesai lalu jalankan ulang build_exe.ps1."
}

if ($OneFile) {
    $env:ATRO_ONEFILE = "1"
    $PyDistPath = $DistDir
    Write-Host "[ATRO Build] Mode: --onefile"
} else {
    $env:ATRO_ONEFILE = "0"
    $PyDistPath = $StageDist
    if (Test-Path $StageDist) {
        Remove-Item -Path $StageDist -Recurse -Force
    }
    New-Item -ItemType Directory -Path $StageDist -Force | Out-Null
    Write-Host "[ATRO Build] Mode: --onedir (staging ke $StageDist agar data user tetap aman)"
}

$argsList = @(
    "--noconfirm",
    "--workpath", $WorkDir,
    "--distpath", $PyDistPath
)
if ($Clean) {
    $argsList += "--clean"
}
$argsList += $SpecFile

try {
    Write-Host "[ATRO Build] Menjalankan PyInstaller..."
    & $PyInstaller @argsList
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller gagal dengan exit code $LASTEXITCODE"
    }

    if (-not $OneFile) {
        $StagedTarget = Join-Path $StageDist "ATRO"
        $StagedExe = Join-Path $StagedTarget "ATRO.exe"
        $StagedInternal = Join-Path $StagedTarget "_internal"
        if (-not (Test-Path $StagedExe)) {
            throw "Staged executable tidak ditemukan di '$StagedExe'"
        }

        New-Item -ItemType Directory -Path $OnedirTarget -Force | Out-Null
        $DestExe = Join-Path $OnedirTarget "ATRO.exe"
        $DestInternal = Join-Path $OnedirTarget "_internal"

        if (Test-Path $DestExe) {
            Remove-Item -Path $DestExe -Force
        }
        if (Test-Path $DestInternal) {
            Remove-Item -Path $DestInternal -Recurse -Force
        }

        Move-Item -Path $StagedExe -Destination $DestExe -Force
        if (Test-Path $StagedInternal) {
            Move-Item -Path $StagedInternal -Destination $DestInternal -Force
        }
        Write-Host "[ATRO Build] Memperbarui ATRO.exe & _internal tanpa menyentuh workspace/config user."
    }
} finally {
    if (-not $OneFile -and (Test-Path $StageDist)) {
        Remove-Item -Path $StageDist -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($OneFile) {
    $ExePath = Join-Path $DistDir "ATRO.exe"
} else {
    $ExePath = Join-Path $OnedirTarget "ATRO.exe"
}

if (-not (Test-Path $ExePath)) {
    throw "Build selesai tetapi executable tidak ditemukan di '$ExePath'"
}

Write-Host "[ATRO Build] Sukses! Executable siap di: $ExePath"
