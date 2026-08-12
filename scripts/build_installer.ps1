[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$SourceDirectory,

    [ValidateNotNullOrEmpty()]
    [string]$OutputDirectory = '',

    [ValidateNotNullOrEmpty()]
    [string]$IsccPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$installerScript = Join-Path $projectRoot 'packaging\installer.iss'
$expectedFileName = 'RenpyThiefPatch-v0.1.2-setup-x64.exe'

function Resolve-ExistingFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    $resolved = Resolve-Path -LiteralPath $Path -ErrorAction Stop
    if (!(Test-Path -LiteralPath $resolved.Path -PathType Leaf)) {
        throw "Expected a file: $($resolved.Path)"
    }
    return $resolved.Path
}

function Resolve-Iscc {
    param([string]$RequestedPath)

    if (![string]::IsNullOrWhiteSpace($RequestedPath)) {
        return Resolve-ExistingFile -Path $RequestedPath
    }

    $command = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $candidates = @()
    if (![string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $candidates += Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
    }
    if (![string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $candidates += Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'
    }
    if (![string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
    }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path
        }
    }

    throw 'Inno Setup Compiler was not found. Install Inno Setup 6.7.3 or pass -IsccPath explicitly.'
}

function Assert-RequiredInputs {
    param([Parameter(Mandatory = $true)][string]$Root)

    $requiredFiles = @(
        'RenpyThiefPatch.exe',
        'LaunchPatch.cmd',
        'QUICK_START.txt',
        'README.md',
        'LICENSE',
        'THIRD_PARTY_NOTICES.md',
        'SOURCE_AVAILABILITY.md',
        'THIRD_PARTY_SOURCE_MANIFEST.txt',
        'router\translate_bridge.exe',
        'router\start_routed_translator.ps1',
        'router\ipcroute.dll',
        'router\netinject.exe',
        'router\guardlaunch.exe',
        'router\versionguard.dll',
        'router\versionguard.ini'
    )
    $requiredDirectories = @('_internal', 'router\_internal', 'licenses')

    $missing = @()
    foreach ($relativePath in $requiredFiles) {
        if (!(Test-Path -LiteralPath (Join-Path $Root $relativePath) -PathType Leaf)) {
            $missing += $relativePath
        }
    }
    foreach ($relativePath in $requiredDirectories) {
        if (!(Test-Path -LiteralPath (Join-Path $Root $relativePath) -PathType Container)) {
            $missing += "$relativePath\"
        }
    }
    if ($missing.Count -gt 0) {
        throw "Portable source directory is incomplete; missing: $($missing -join ', ')"
    }
}

function Assert-PeFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        if ($stream.Length -lt 68) {
            throw "Installer output is too small to be a PE file: $Path"
        }
        if ($stream.ReadByte() -ne 0x4D -or $stream.ReadByte() -ne 0x5A) {
            throw "Installer output does not have a valid DOS signature: $Path"
        }

        $stream.Position = 0x3C
        $reader = [IO.BinaryReader]::new($stream, [Text.Encoding]::ASCII, $true)
        try {
            $peOffset = $reader.ReadInt32()
        } finally {
            $reader.Dispose()
        }
        if ($peOffset -lt 0x40 -or $peOffset -gt ($stream.Length - 4)) {
            throw "Installer output has an invalid PE header offset: $Path"
        }

        $stream.Position = $peOffset
        if ($stream.ReadByte() -ne 0x50 -or
            $stream.ReadByte() -ne 0x45 -or
            $stream.ReadByte() -ne 0x00 -or
            $stream.ReadByte() -ne 0x00) {
            throw "Installer output does not have a valid PE signature: $Path"
        }
    } finally {
        $stream.Dispose()
    }
}

$sourceRoot = (Resolve-Path -LiteralPath $SourceDirectory -ErrorAction Stop).Path
if (!(Test-Path -LiteralPath $sourceRoot -PathType Container)) {
    throw "Portable source directory not found: $sourceRoot"
}
Assert-RequiredInputs -Root $sourceRoot

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $outputRoot = Join-Path $projectRoot 'release'
} else {
    $outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
}
New-Item -ItemType Directory -Path $outputRoot -Force -ErrorAction Stop | Out-Null
$outputRoot = (Resolve-Path -LiteralPath $outputRoot -ErrorAction Stop).Path

$sourceBoundary = $sourceRoot.TrimEnd('\') + '\'
$outputBoundary = $outputRoot.TrimEnd('\') + '\'
if ($outputBoundary.StartsWith($sourceBoundary, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputDirectory must not be inside SourceDirectory.'
}

$compiler = Resolve-Iscc -RequestedPath $IsccPath

if (!(Test-Path -LiteralPath $installerScript -PathType Leaf)) {
    throw "Installer definition not found: $installerScript"
}

# ISCC.exe 6.7.3 reports 0.0.0.0 in its Windows file-version resource.
# The installer definition checks ISPP's built-in VER constant instead, which
# is the compiler's authoritative version and fails the build unless it is
# exactly 6.7.3.
Write-Host "Inno Setup:     $compiler (installer script requires 6.7.3)"
Write-Host "Portable input: $sourceRoot"
Write-Host "Output folder:  $outputRoot"

$installerPath = Join-Path $outputRoot $expectedFileName
if (Test-Path -LiteralPath $installerPath -PathType Leaf) {
    Remove-Item -LiteralPath $installerPath -Force -ErrorAction Stop
}

& $compiler '/Qp' "/DSourceDirectory=$sourceRoot" "/DOutputDirectory=$outputRoot" $installerScript
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed with exit code $LASTEXITCODE."
}

if (!(Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "Inno Setup did not create the expected asset: $installerPath"
}
Assert-PeFile -Path $installerPath

$hash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "Installer: $installerPath"
Write-Host "SHA-256:  $hash"

[PSCustomObject]@{
    Path = $installerPath
    SHA256 = $hash
    Bytes = (Get-Item -LiteralPath $installerPath).Length
}
