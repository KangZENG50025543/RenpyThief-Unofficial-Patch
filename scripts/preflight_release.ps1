[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$')]
    [string]$Version = '1.0.1',

    [string]$Python = 'python.exe',

    [switch]$SkipSourceArchives
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 defaults to the ANSI code page. Git emits UTF-8 paths.
$utf8 = New-Object System.Text.UTF8Encoding $false
try {
    [Console]::InputEncoding = $utf8
    [Console]::OutputEncoding = $utf8
} catch {
}
$OutputEncoding = $utf8

$scriptPath = $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($scriptPath)) {
    throw 'Run this script from its .ps1 file; do not paste or dot-source it.'
}
$scriptDirectory = Split-Path -Parent ([IO.Path]::GetFullPath($scriptPath))
$repositoryRoot = Split-Path -Parent $scriptDirectory
$expectedRoot = [IO.Path]::GetFullPath($repositoryRoot).TrimEnd('\')
$localizedLauncherName = [string]::Concat(@(
    [char]0x542F, [char]0x52A8, [char]0x975E, [char]0x5B98,
    [char]0x65B9, [char]0x8865, [char]0x4E01
)) + '.cmd'

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE."
    }
}

$gitRoot = (& git -C $repositoryRoot rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($gitRoot)) {
    throw 'This directory is not an independent Git repository.'
}
$actualRoot = [IO.Path]::GetFullPath(($gitRoot | Select-Object -First 1)).TrimEnd('\')
if (-not $actualRoot.Equals($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe Git root: expected $expectedRoot, got $actualRoot"
}

$trackedFiles = @(& git -c core.quotepath=false -C $repositoryRoot ls-files)
if ($LASTEXITCODE -ne 0 -or $trackedFiles.Count -eq 0) {
    throw 'No tracked source files were found.'
}

$forbiddenPathPattern = '(?i)(^|/)(API(?:_siliconflow)?\.txt|[^/]*_api\.txt|api_[^/]*\.txt|[^/]*(?:token|secret)[^/]*\.txt|user|hwid|settings\.json|RenpyThief\.exe|RenpyUpdater\.exe|[^/]+\.(?:exe|dll|lib|exp|pdb|zip|log|pcap|pcapng|har|dmp|etl))$'
$forbiddenTracked = @($trackedFiles | Where-Object {
    $_.Replace('\', '/') -match $forbiddenPathPattern
})
if ($forbiddenTracked.Count -gt 0) {
    throw "Forbidden tracked files: $($forbiddenTracked -join ', ')"
}

$secretPatterns = @(
    'sk-[A-Za-z0-9_-]{16,}',
    '-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    '(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*["''][A-Za-z0-9_+/=-]{16,}["'']'
)
foreach ($relativePath in $trackedFiles) {
    $fullPath = Join-Path $repositoryRoot $relativePath
    $item = Get-Item -LiteralPath $fullPath
    if ($item.Length -gt 2MB) {
        continue
    }
    $bytes = [IO.File]::ReadAllBytes($fullPath)
    if ($bytes -contains 0) {
        throw "Tracked file contains NUL bytes and needs manual review: $relativePath"
    }
    $text = [Text.Encoding]::UTF8.GetString($bytes)
    foreach ($pattern in $secretPatterns) {
        if ($text -match $pattern) {
            throw "Possible secret in tracked file: $relativePath"
        }
    }
}

foreach ($relativePath in @($trackedFiles | Where-Object { $_ -like '*.ps1' })) {
    $tokens = $null
    $parseErrors = $null
    [Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $repositoryRoot $relativePath),
        [ref]$tokens,
        [ref]$parseErrors
    ) | Out-Null
    if ($parseErrors.Count -gt 0) {
        throw "PowerShell parse failure in $relativePath`: $($parseErrors[0].Message)"
    }
}

$pythonExecutable = (Get-Command $Python -ErrorAction Stop).Source
$oldQtPlatform = $env:QT_QPA_PLATFORM
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    Push-Location $repositoryRoot
    try {
        Invoke-External -FilePath $pythonExecutable -Arguments @(
            '-m', 'unittest', 'discover', '-s', 'tests', '-v'
        )
        Invoke-External -FilePath $pythonExecutable -Arguments @(
            '.\run_patch.py', '--smoke-test'
        )
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:QT_QPA_PLATFORM = $oldQtPlatform
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$releaseName = "RenpyThiefPatch-v$Version-portable-x64"
$archivePath = Join-Path $repositoryRoot "release\$releaseName.zip"
$installerName = "RenpyThiefPatch-v$Version-setup-x64.exe"
$installerPath = Join-Path $repositoryRoot "release\$installerName"
$checksumPath = Join-Path $repositoryRoot 'release\SHA256SUMS.txt'
if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
    throw "Release ZIP not found: $archivePath"
}
if (-not (Test-Path -LiteralPath $checksumPath -PathType Leaf)) {
    throw "Release checksum not found: $checksumPath"
}
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "Release installer not found: $installerPath"
}

$installerItem = Get-Item -LiteralPath $installerPath
if ($installerItem.Length -le 0) {
    throw "Release installer is empty: $installerPath"
}
$installerStream = [IO.File]::Open(
    $installerPath,
    [IO.FileMode]::Open,
    [IO.FileAccess]::Read,
    [IO.FileShare]::Read
)
try {
    if ($installerStream.Length -lt 68 -or
        $installerStream.ReadByte() -ne 0x4D -or
        $installerStream.ReadByte() -ne 0x5A) {
        throw "Release installer does not have an MZ executable signature: $installerPath"
    }

    $installerStream.Position = 0x3C
    $installerReader = [IO.BinaryReader]::new(
        $installerStream,
        [Text.Encoding]::ASCII,
        $true
    )
    try {
        $peOffset = $installerReader.ReadInt32()
    }
    finally {
        $installerReader.Dispose()
    }
    if ($peOffset -lt 0x40 -or $peOffset -gt ($installerStream.Length - 4)) {
        throw "Release installer has an invalid PE header offset: $installerPath"
    }
    $installerStream.Position = $peOffset
    if ($installerStream.ReadByte() -ne 0x50 -or
        $installerStream.ReadByte() -ne 0x45 -or
        $installerStream.ReadByte() -ne 0x00 -or
        $installerStream.ReadByte() -ne 0x00) {
        throw "Release installer does not have a PE executable signature: $installerPath"
    }
}
finally {
    $installerStream.Dispose()
}

$releaseAssetPaths = @($archivePath, $installerPath) |
    Sort-Object { [IO.Path]::GetFileName($_) }
$expectedChecksumLines = @(
    $releaseAssetPaths | ForEach-Object {
        $hash = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $([IO.Path]::GetFileName($_))"
    }
)
$expectedChecksumText = ($expectedChecksumLines -join "`n") + "`n"
$actualChecksumText = (
    Get-Content -Raw -LiteralPath $checksumPath -Encoding ASCII
).Replace("`r`n", "`n")
if ($actualChecksumText -cne $expectedChecksumText) {
    throw 'SHA256SUMS.txt must contain exactly the sorted installer and portable ZIP hashes.'
}

$archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    $separator = [char]92
    $prefix = $releaseName + $separator
    $entryNames = @($archive.Entries | ForEach-Object { $_.FullName })
    $requiredEntries = @(
        'RenpyThiefPatch.exe',
        'LaunchPatch.cmd',
        $localizedLauncherName,
        'QUICK_START.txt',
        'router\translate_bridge.exe',
        'router\ipcroute.dll',
        'router\netinject.exe',
        'router\guardlaunch.exe',
        'router\versionguard.dll',
        'router\versionguard.ini',
        'README.md',
        'LICENSE',
        'COPYRIGHT',
        'SOURCE_AVAILABILITY.md',
        'THIRD_PARTY_SOURCE_MANIFEST.txt',
        'THIRD_PARTY_NOTICES.md',
        'DEPENDENCIES.txt'
    )
    $missingEntries = @($requiredEntries | Where-Object {
        ($prefix + $_) -notin $entryNames
    })
    if ($missingEntries.Count -gt 0) {
        throw "Release ZIP is missing: $($missingEntries -join ', ')"
    }
    $forbiddenReleasePattern = '(?i)(^|/)(API(?:_siliconflow)?\.txt|[^/]*_api\.txt|api_[^/]*\.txt|[^/]*(?:token|secret)[^/]*\.txt|user|hwid|settings\.json|RenpyThief\.exe|RenpyUpdater\.exe|RenpyThief(?:[_-][^/]*)?\.zip|[^/]+\.(?:log|pdb|pcap|pcapng|har|dmp|etl))$'
    $forbiddenArchiveEntries = @($entryNames | ForEach-Object {
        $_.Replace($separator, '/')
    } | Where-Object { $_ -match $forbiddenReleasePattern })
    if ($forbiddenArchiveEntries.Count -gt 0) {
        throw "Forbidden release entries: $($forbiddenArchiveEntries -join ', ')"
    }

    $textEntryPattern = '(?i)\.(?:cmd|ini|json|md|ps1|py|txt|yaml|yml)$'
    foreach ($entry in @($archive.Entries | Where-Object {
        $_.Length -le 2MB -and $_.FullName -match $textEntryPattern
    })) {
        $entryStream = $entry.Open()
        try {
            $reader = [IO.StreamReader]::new(
                $entryStream,
                [Text.Encoding]::UTF8,
                $true
            )
            try {
                $entryText = $reader.ReadToEnd()
            }
            finally {
                $reader.Dispose()
            }
        }
        finally {
            $entryStream.Dispose()
        }
        foreach ($pattern in $secretPatterns) {
            if ($entryText -match $pattern) {
                throw "Possible secret in release ZIP entry: $($entry.FullName)"
            }
        }
    }
}
finally {
    $archive.Dispose()
}

if (-not $SkipSourceArchives) {
    $sourceScript = Join-Path $repositoryRoot 'scripts\prepare_release_sources.ps1'
    $sourceDirectory = Join-Path $repositoryRoot "release\source-assets-v$Version"
    Invoke-External -FilePath 'powershell.exe' -Arguments @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $sourceScript,
        '-Version', $Version, '-OutputDirectory', $sourceDirectory, '-VerifyOnly'
    )
}

Write-Host "Preflight passed for $releaseName."
Write-Host "Tracked source files: $($trackedFiles.Count)"
Write-Host "Portable SHA-256: $((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant())"
Write-Host "Installer SHA-256: $((Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant())"
