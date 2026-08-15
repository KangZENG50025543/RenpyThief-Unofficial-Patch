[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$')]
    [string]$Version = '1.0.0',
    [string]$Python = '',
    [string]$IsccPath = '',
    [switch]$PublicRelease
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceRouter = Join-Path $projectRoot 'router'
$workRoot = Join-Path $projectRoot 'build'
$distRoot = Join-Path $projectRoot 'dist'
$releaseParent = Join-Path $projectRoot 'release'
$releaseName = "RenpyThiefPatch-v$Version-portable-x64"
$releaseRoot = Join-Path $releaseParent $releaseName
$archivePath = Join-Path $releaseParent "$releaseName.zip"
$installerName = "RenpyThiefPatch-v$Version-setup-x64.exe"
$installerPath = Join-Path $releaseParent $installerName
$checksumPath = Join-Path $releaseParent 'SHA256SUMS.txt'
$versionModule = Join-Path $projectRoot 'src\renpy_patch\__init__.py'
$localizedLauncherName = [string]::Concat(@(
    [char]0x542F, [char]0x52A8, [char]0x975E, [char]0x5B98,
    [char]0x65B9, [char]0x8865, [char]0x4E01
)) + '.cmd'

$versionSource = Get-Content -LiteralPath $versionModule -Raw -Encoding UTF8
if ($versionSource -notmatch ('__version__\s*=\s*["'']' + [regex]::Escape($Version) + '["'']')) {
    throw "Build version $Version does not match src\renpy_patch\__init__.py."
}

function Assert-ChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Child
    )
    $parentFull = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    $childFull = [IO.Path]::GetFullPath($Child)
    if (!$childFull.StartsWith($parentFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing path outside project: $childFull"
    }
}

foreach ($path in @(
    $workRoot, $distRoot, $releaseParent, $releaseRoot, $archivePath,
    $installerPath, $checksumPath
)) {
    Assert-ChildPath -Parent $projectRoot -Child $path
}
foreach ($required in @(
    (Join-Path $projectRoot 'run_patch.py'),
    (Join-Path $projectRoot 'README.md'),
    (Join-Path $projectRoot 'packaging\LaunchPatch.cmd'),
    (Join-Path $projectRoot 'packaging\QUICK_START.txt'),
    (Join-Path $projectRoot 'THIRD_PARTY_NOTICES.md'),
    (Join-Path $sourceRouter 'translate_bridge.py'),
    (Join-Path $sourceRouter 'start_routed_translator.ps1'),
    (Join-Path $sourceRouter 'ipcroute.dll'),
    (Join-Path $sourceRouter 'netinject.exe'),
    (Join-Path $sourceRouter 'guardlaunch.exe'),
    (Join-Path $sourceRouter 'versionguard.dll'),
    (Join-Path $sourceRouter 'versionguard.ini')
)) {
    if (!(Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required build input not found: $required"
    }
}

if ($PublicRelease) {
    $publicReleaseFiles = @(
        'LICENSE',
        'COPYRIGHT',
        'SOURCE_AVAILABILITY.md',
        'THIRD_PARTY_SOURCE_MANIFEST.txt',
        'scripts\prepare_release_sources.ps1',
        'scripts\build_installer.ps1',
        'packaging\installer.iss',
        'packaging\QUICK_START.txt',
        'THIRD_PARTY_NOTICES.md',
        'requirements-lock.txt',
        'licenses\MinHook-LICENSE.txt',
        'licenses\Inno-Setup-6.7.3-LICENSE.txt',
        'licenses\Qt-5.15.2-LICENSE.txt',
        'licenses\Python-3.12-LICENSE.txt',
        'licenses\PyInstaller-6.22.0-COPYING.txt',
        'licenses\PyInstaller-hooks-contrib-2026.6-LICENSE.txt',
        'licenses\PyQt5-sip-12.19.0-LICENSE.txt',
        'licenses\keyring-25.7.0-LICENSE.txt'
    )
    $missingPublicReleaseFiles = @($publicReleaseFiles | Where-Object {
        !(Test-Path -LiteralPath (Join-Path $projectRoot $_) -PathType Leaf)
    })
    if ($missingPublicReleaseFiles.Count -gt 0) {
        throw "Public release refused; missing licensing inputs: $($missingPublicReleaseFiles -join ', ')"
    }
}

$pythonExecutable = if ([string]::IsNullOrWhiteSpace($Python)) {
    (Get-Command python.exe -ErrorAction Stop).Source
} else {
    (Resolve-Path -LiteralPath $Python -ErrorAction Stop).Path
}
if ($pythonExecutable -match '[^\x00-\x7F]') {
    throw 'PyInstaller Qt builds require the Python/venv path to contain ASCII characters only.'
}
New-Item -ItemType Directory -Path (Join-Path $workRoot 'spec') -Force -ErrorAction Stop | Out-Null

& $pythonExecutable -m PyInstaller --noconfirm --clean --windowed --onedir --noupx `
    --name RenpyThiefPatch `
    --paths (Join-Path $projectRoot 'src') `
    --hidden-import keyring.backends.Windows `
    --collect-submodules keyring.backends `
    --workpath (Join-Path $workRoot 'gui') `
    --specpath (Join-Path $workRoot 'spec') `
    --distpath $distRoot `
    (Join-Path $projectRoot 'run_patch.py')
if ($LASTEXITCODE -ne 0) {
    throw "GUI PyInstaller build failed with exit code $LASTEXITCODE."
}

& $pythonExecutable -m PyInstaller --noconfirm --clean --console --onedir --noupx `
    --name translate_bridge `
    --workpath (Join-Path $workRoot 'bridge') `
    --specpath (Join-Path $workRoot 'spec') `
    --distpath (Join-Path $workRoot 'bridge-dist') `
    (Join-Path $sourceRouter 'translate_bridge.py')
if ($LASTEXITCODE -ne 0) {
    throw "Bridge PyInstaller build failed with exit code $LASTEXITCODE."
}

if (Test-Path -LiteralPath $releaseRoot) {
    $resolvedRelease = (Resolve-Path -LiteralPath $releaseRoot).Path
    Assert-ChildPath -Parent $releaseParent -Child $resolvedRelease
    Remove-Item -LiteralPath $resolvedRelease -Recurse -Force -ErrorAction Stop
}
New-Item -ItemType Directory -Path $releaseRoot -ErrorAction Stop | Out-Null
Copy-Item -Path (Join-Path $distRoot 'RenpyThiefPatch\*') -Destination $releaseRoot -Recurse -Force

$releaseRouter = Join-Path $releaseRoot 'router'
New-Item -ItemType Directory -Path $releaseRouter -ErrorAction Stop | Out-Null
Copy-Item -Path (Join-Path $workRoot 'bridge-dist\translate_bridge\*') `
    -Destination $releaseRouter -Recurse -Force
foreach ($name in @(
    'start_routed_translator.ps1', 'ipcroute.dll', 'netinject.exe',
    'guardlaunch.exe', 'versionguard.dll', 'versionguard.ini'
)) {
    Copy-Item -LiteralPath (Join-Path $sourceRouter $name) -Destination $releaseRouter
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'packaging\LaunchPatch.cmd') `
    -Destination (Join-Path $releaseRoot 'LaunchPatch.cmd') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'packaging\LaunchPatch.cmd') `
    -Destination (Join-Path $releaseRoot $localizedLauncherName) -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'packaging\QUICK_START.txt') `
    -Destination (Join-Path $releaseRoot 'QUICK_START.txt') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $projectRoot 'UPDATE_GUARD_CONTRACT.md') `
    -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $projectRoot 'THIRD_PARTY_NOTICES.md') `
    -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $projectRoot 'SOURCE_AVAILABILITY.md') `
    -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $projectRoot 'THIRD_PARTY_SOURCE_MANIFEST.txt') `
    -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $projectRoot 'COPYRIGHT') `
    -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $projectRoot 'requirements-lock.txt') `
    -Destination (Join-Path $releaseRoot 'DEPENDENCIES.txt')
if (Test-Path -LiteralPath (Join-Path $projectRoot 'LICENSE') -PathType Leaf) {
    Copy-Item -LiteralPath (Join-Path $projectRoot 'LICENSE') -Destination $releaseRoot
}
if (Test-Path -LiteralPath (Join-Path $projectRoot 'licenses') -PathType Container) {
    Copy-Item -LiteralPath (Join-Path $projectRoot 'licenses') `
        -Destination $releaseRoot -Recurse -Force
}

$staleOutputs = @($archivePath, $checksumPath)
if ($PublicRelease) {
    $staleOutputs += $installerPath
}
foreach ($output in $staleOutputs) {
    if (Test-Path -LiteralPath $output -PathType Leaf) {
        Remove-Item -LiteralPath $output -Force -ErrorAction Stop
    }
}
Compress-Archive -LiteralPath $releaseRoot -DestinationPath $archivePath `
    -CompressionLevel Optimal

$releaseAssets = @($archivePath)
if ($PublicRelease) {
    $installerBuilder = Join-Path $projectRoot 'scripts\build_installer.ps1'
    $installerArguments = @{
        SourceDirectory = $releaseRoot
        OutputDirectory = $releaseParent
    }
    if (![string]::IsNullOrWhiteSpace($IsccPath)) {
        $installerArguments.IsccPath = $IsccPath
    }
    & $installerBuilder @installerArguments
    if (!(Test-Path -LiteralPath $installerPath -PathType Leaf)) {
        throw "Installer build did not create the expected asset: $installerPath"
    }
    $releaseAssets += $installerPath
}

$checksumLines = @(
    $releaseAssets |
        Sort-Object { [IO.Path]::GetFileName($_) } |
        ForEach-Object {
            $hash = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
            "$hash  $([IO.Path]::GetFileName($_))"
        }
)
Set-Content -LiteralPath $checksumPath -Encoding ASCII -Value $checksumLines

Write-Host "Release directory: $releaseRoot"
Write-Host "Portable archive:  $archivePath"
if ($PublicRelease) {
    Write-Host "Installer:         $installerPath"
}
Write-Host "SHA-256 list:      $checksumPath"
