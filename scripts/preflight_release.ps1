[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$')]
    [string]$Version = '0.1.0',

    [string]$Python = 'python.exe',

    [switch]$SkipSourceArchives
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptPath = $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($scriptPath)) {
    throw 'Run this script from its .ps1 file; do not paste or dot-source it.'
}
$scriptDirectory = Split-Path -Parent ([IO.Path]::GetFullPath($scriptPath))
$repositoryRoot = Split-Path -Parent $scriptDirectory
$expectedRoot = [IO.Path]::GetFullPath($repositoryRoot).TrimEnd('\')

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
$releaseName = "RenpyThiefPatch-v$Version-windows-x64"
$archivePath = Join-Path $repositoryRoot "release\$releaseName.zip"
$checksumPath = Join-Path $repositoryRoot 'release\SHA256SUMS.txt'
if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
    throw "Release ZIP not found: $archivePath"
}
if (-not (Test-Path -LiteralPath $checksumPath -PathType Leaf)) {
    throw "Release checksum not found: $checksumPath"
}
$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumLine = (Get-Content -Raw -LiteralPath $checksumPath -Encoding ASCII).Trim()
if ($checksumLine -cne "$archiveHash  $releaseName.zip") {
    throw 'SHA256SUMS.txt does not exactly match the release ZIP.'
}

$archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    $separator = [char]92
    $prefix = $releaseName + $separator
    $entryNames = @($archive.Entries | ForEach-Object { $_.FullName })
    $requiredEntries = @(
        'RenpyThiefPatch.exe',
        'LaunchPatch.cmd',
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
    $forbiddenReleasePattern = '(?i)(^|/)(API(?:_siliconflow)?\.txt|[^/]*_api\.txt|api_[^/]*\.txt|[^/]*(?:token|secret)[^/]*\.txt|user|hwid|settings\.json|RenpyThief\.exe|RenpyUpdater\.exe|[^/]+\.(?:log|pdb|pcap|pcapng|har|dmp|etl))$'
    $forbiddenArchiveEntries = @($entryNames | ForEach-Object {
        $_.Replace($separator, '/')
    } | Where-Object { $_ -match $forbiddenReleasePattern })
    if ($forbiddenArchiveEntries.Count -gt 0) {
        throw "Forbidden release entries: $($forbiddenArchiveEntries -join ', ')"
    }
}
finally {
    $archive.Dispose()
}

if (-not $SkipSourceArchives) {
    $sourceScript = Join-Path $repositoryRoot 'scripts\prepare_release_sources.ps1'
    Invoke-External -FilePath 'powershell.exe' -Arguments @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $sourceScript, '-VerifyOnly'
    )
}

Write-Host "Preflight passed for $releaseName."
Write-Host "Tracked source files: $($trackedFiles.Count)"
Write-Host "Release SHA-256: $archiveHash"
