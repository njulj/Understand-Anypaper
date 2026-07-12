$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ServerDir = Join-Path $RootDir "apps/server"
$WebDir = Join-Path $RootDir "apps/web"
$BackendDir = Join-Path $WebDir "backend"
$PyInstallerTmp = Join-Path $RootDir ".tmp/pyinstaller/windows"
$UvCacheDir = Join-Path $RootDir ".tmp/uv-cache"
$WindowsSignEnabled = $env:PAG_WINDOWS_SIGN
$RebuildBackend = $env:PAG_REBUILD_BACKEND
$BackendExecutable = Join-Path $BackendDir "server.exe"
$BackendStampFile = Join-Path $BackendDir ".packaging-version"
$ReleaseBackendExecutable = Join-Path $WebDir "release/win-unpacked/resources/backend/server.exe"
$ReleaseBackendStampFile = Join-Path $WebDir "release/win-unpacked/resources/backend/.packaging-version"
$RequiredBackendPackagingVersion = "desktop-backend-v3"

New-Item -ItemType Directory -Force -Path $BackendDir | Out-Null
New-Item -ItemType Directory -Force -Path $PyInstallerTmp | Out-Null
New-Item -ItemType Directory -Force -Path $UvCacheDir | Out-Null

if (!(Test-Path (Join-Path $WebDir "node_modules"))) {
  npm --prefix $WebDir install
}

npm --prefix $WebDir run doctor:bindings

function Test-BackendRebuildRequired {
  if ($RebuildBackend -eq "1") {
    return $true
  }

  if (!(Test-Path $BackendExecutable) -or !(Test-Path $BackendStampFile)) {
    return $true
  }

  if ((Get-Content $BackendStampFile -Raw).Trim() -ne $RequiredBackendPackagingVersion) {
    return $true
  }

  if ((Get-Item $ServerDir).LastWriteTimeUtc -gt (Get-Item $BackendExecutable).LastWriteTimeUtc) {
    return $true
  }

  if ((Get-Item (Join-Path $ServerDir "pyproject.toml")).LastWriteTimeUtc -gt (Get-Item $BackendExecutable).LastWriteTimeUtc) {
    return $true
  }

  if ((Get-Item $PSCommandPath).LastWriteTimeUtc -gt (Get-Item $BackendExecutable).LastWriteTimeUtc) {
    return $true
  }

  $changedPython = Get-ChildItem (Join-Path $ServerDir "understand_anypaper") -Recurse -File -Filter *.py |
    Where-Object { $_.LastWriteTimeUtc -gt (Get-Item $BackendExecutable).LastWriteTimeUtc } |
    Select-Object -First 1
  return $null -ne $changedPython
}

if (!(Test-Path $BackendExecutable) -and (Test-Path $ReleaseBackendExecutable) -and (Test-Path $ReleaseBackendStampFile) -and ((Get-Content $ReleaseBackendStampFile -Raw).Trim() -eq $RequiredBackendPackagingVersion)) {
  Write-Host "Restoring packaged backend from existing release artifact at $ReleaseBackendExecutable."
  Copy-Item $ReleaseBackendExecutable $BackendExecutable -Force
  Copy-Item $ReleaseBackendStampFile $BackendStampFile -Force
}

if (Test-BackendRebuildRequired) {
  Write-Host "Rebuilding packaged backend to match current desktop packaging inputs."
  Get-ChildItem -Path $BackendDir -Force | Remove-Item -Recurse -Force
  $env:UV_CACHE_DIR = $UvCacheDir
  uv run --project $ServerDir --with pyinstaller pyinstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name server `
    --hidden-import agent_framework.openai `
    --hidden-import agent_framework_openai `
    --collect-all agent_framework_openai `
    --distpath $BackendDir `
    --workpath (Join-Path $PyInstallerTmp "build") `
    --specpath (Join-Path $PyInstallerTmp "spec") `
    --paths $ServerDir `
    (Join-Path $ServerDir "understand_anypaper/desktop_server.py")
  Set-Content -Path $BackendStampFile -Value $RequiredBackendPackagingVersion
}
else {
  Write-Host "Reusing existing packaged backend at $BackendExecutable (set PAG_REBUILD_BACKEND=1 to rebuild)."
}

npm --prefix $WebDir run build

if ($WindowsSignEnabled -ne "1") {
  Write-Host "Building unsigned Windows app bundle (set PAG_WINDOWS_SIGN=1 to enable code signing)."
  $env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
}

npm --prefix $WebDir run electron:dist -- --win
