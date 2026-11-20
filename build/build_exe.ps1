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
$PreserveDir = Join-Path $WorkDir "_preserve_dist"
$UserItems = @("config.json", ".env", "workspace", "bin", "backsound")

if (-not (Test-Path $PyInstaller)) {
    throw "PyInstaller tidak ditemukan di '$PyInstaller'. Pastikan .venv sudah terinstall."
}

$running = Get-Process -Name "ATRO" -ErrorAction SilentlyContinue
if ($running) {
    throw "ATRO.exe sedang berjalan (PID: $($running.Id -join ', ')). Tutup jendela ATRO setelah job selesai lalu jalankan ulang build_exe.ps1."
}

if ($OneFile) {
    $env:ATRO_ONEFILE = "1"
    Write-Host "[ATRO Build] Mode: --onefile"
} else {
    $env:ATRO_ONEFILE = "0"
    Write-Host "[ATRO Build] Mode: --onedir"
}

# Backup user runtime data from dist\ATRO before PyInstaller --onedir cleans it
$BackedUpItems = @()
if (-not $OneFile -and (Test-Path $OnedirTarget)) {
    if (Test-Path $PreserveDir) {
        Remove-Item -Path $PreserveDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $PreserveDir -Force | Out-Null

    foreach ($item in $UserItems) {
        $src = Join-Path $OnedirTarget $item
        if (Test-Path $src) {
            $dst = Join-Path $PreserveDir $item
            Move-Item -Path $src -Destination $dst -Force
            $BackedUpItems += $item
            Write-Host "[ATRO Build] Mengamankan data user: $item"
        }
    }
}

$argsList = @(
    "--noconfirm",
    "--workpath", $WorkDir,
    "--distpath", $DistDir
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
} finally {
    if (-not $OneFile -and $BackedUpItems.Count -gt 0) {
        New-Item -ItemType Directory -Path $OnedirTarget -Force | Out-Null
        foreach ($item in $BackedUpItems) {
            $src = Join-Path $PreserveDir $item
            $dst = Join-Path $OnedirTarget $item
            if (Test-Path $src) {
                if (Test-Path $dst) {
                    Remove-Item -Path $dst -Recurse -Force
                }
                Move-Item -Path $src -Destination $dst -Force
                Write-Host "[ATRO Build] Memulihkan data user: $item"
            }
        }
        if (Test-Path $PreserveDir) {
            Remove-Item -Path $PreserveDir -Recurse -Force
        }
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
