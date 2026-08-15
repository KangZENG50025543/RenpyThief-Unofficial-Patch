[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$')]
    [string]$Version = '1.0.0',

    [Parameter()]
    [string]$OutputDirectory,

    [Parameter()]
    [switch]$DryRun,

    [Parameter()]
    [switch]$VerifyOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($DryRun -and $VerifyOnly) {
    throw '-DryRun and -VerifyOnly cannot be used together.'
}

$scriptPath = $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($scriptPath)) {
    throw 'Cannot determine this script path. Run the .ps1 file instead of pasting or dot-sourcing it.'
}

$scriptDirectory = Split-Path -Parent ([System.IO.Path]::GetFullPath($scriptPath))
$repositoryRoot = Split-Path -Parent $scriptDirectory
$manifestPath = Join-Path $repositoryRoot 'THIRD_PARTY_SOURCE_MANIFEST.txt'
$lockPath = Join-Path $repositoryRoot 'requirements-lock.txt'
$nativeReadmePath = Join-Path $repositoryRoot 'native\README.md'

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $repositoryRoot "release\source-assets-v$Version"
}

$outputFullPath = [System.IO.Path]::GetFullPath($OutputDirectory)
$expectedHeader = 'component|version|filename|url|sha256|size_bytes|hash_authority|hash_evidence'
$allowedInitialHosts = @{
    'codeload.github.com' = $true
    'download.qt.io' = $true
    'files.pythonhosted.org' = $true
    'www.python.org' = $true
}
$requiredBaselines = @{
    'MinHook' = '1.3.4'
    'PyQt5' = '5.15.11'
    'Python' = '3.12.7'
    'Qt' = '5.15.2'
}

function Read-SourceManifest {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Source manifest not found: $Path"
    }

    $meaningfulLines = @(
        Get-Content -LiteralPath $Path -Encoding UTF8 |
            Where-Object { $_ -notmatch '^\s*(#|$)' }
    )
    if ($meaningfulLines.Count -lt 2) {
        throw "Source manifest has no data rows: $Path"
    }
    if ($meaningfulLines[0] -cne $expectedHeader) {
        throw "Unexpected source manifest header. Expected: $expectedHeader"
    }

    $parsedRows = @($meaningfulLines | ConvertFrom-Csv -Delimiter '|')
    $normalizedRows = New-Object System.Collections.ArrayList
    $seenComponents = @{}
    $seenFilenames = @{}

    foreach ($row in $parsedRows) {
        $component = ([string]$row.component).Trim()
        $version = ([string]$row.version).Trim()
        $filename = ([string]$row.filename).Trim()
        $urlText = ([string]$row.url).Trim()
        $sha256 = ([string]$row.sha256).Trim().ToLowerInvariant()
        $sizeText = ([string]$row.size_bytes).Trim()
        $hashAuthority = ([string]$row.hash_authority).Trim()
        $hashEvidence = ([string]$row.hash_evidence).Trim()

        if ([string]::IsNullOrWhiteSpace($component) -or
            [string]::IsNullOrWhiteSpace($version) -or
            [string]::IsNullOrWhiteSpace($filename) -or
            [string]::IsNullOrWhiteSpace($urlText) -or
            [string]::IsNullOrWhiteSpace($hashEvidence)) {
            throw 'Every source manifest field must be non-empty.'
        }
        if ($seenComponents.ContainsKey($component)) {
            throw "Duplicate component in source manifest: $component"
        }
        if ($seenFilenames.ContainsKey($filename)) {
            throw "Duplicate filename in source manifest: $filename"
        }
        $seenComponents[$component] = $true
        $seenFilenames[$filename] = $true

        if ($filename -eq '.' -or $filename -eq '..' -or
            $filename -cne [System.IO.Path]::GetFileName($filename) -or
            $filename.IndexOfAny([System.IO.Path]::GetInvalidFileNameChars()) -ge 0) {
            throw "Unsafe filename in source manifest: $filename"
        }
        if ($sha256 -notmatch '^[0-9a-f]{64}$' -or $sha256 -match '^0{64}$') {
            throw "Missing or invalid SHA-256 for $component. Verify it independently before editing the manifest."
        }

        [long]$expectedSize = 0
        if (-not [long]::TryParse($sizeText, [ref]$expectedSize) -or $expectedSize -le 0) {
            throw "Invalid expected size for $component`: $sizeText"
        }

        [uri]$sourceUri = $null
        if (-not [uri]::TryCreate($urlText, [System.UriKind]::Absolute, [ref]$sourceUri) -or
            $sourceUri.Scheme -cne 'https' -or
            -not $allowedInitialHosts.ContainsKey($sourceUri.DnsSafeHost)) {
            throw "Source URL must use HTTPS on an approved upstream host: $urlText"
        }
        if ($hashAuthority -notin @('upstream', 'independent_pinned')) {
            throw "Unknown hash authority for $component`: $hashAuthority"
        }

        [void]$normalizedRows.Add([pscustomobject]@{
            Component = $component
            Version = $version
            Filename = $filename
            SourceUri = $sourceUri
            Sha256 = $sha256
            ExpectedSize = $expectedSize
            HashAuthority = $hashAuthority
            HashEvidence = $hashEvidence
        })
    }

    foreach ($component in $requiredBaselines.Keys) {
        $matches = @($normalizedRows | Where-Object { $_.Component -ceq $component })
        if ($matches.Count -ne 1 -or $matches[0].Version -cne $requiredBaselines[$component]) {
            throw "Manifest baseline mismatch for $component. Expected $($requiredBaselines[$component])."
        }
    }

    return @($normalizedRows)
}

function Assert-RepositoryBaselines {
    if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) {
        throw "Dependency lock not found: $lockPath"
    }
    $lockText = Get-Content -Raw -LiteralPath $lockPath -Encoding UTF8
    foreach ($requiredLine in @(
        'PyQt5==5.15.11',
        'PyQt5-Qt5==5.15.2'
    )) {
        $pattern = '(?m)^\s*' + [regex]::Escape($requiredLine) + '\s*$'
        if ($lockText -notmatch $pattern) {
            throw "requirements-lock.txt no longer contains $requiredLine. Update and re-audit the source manifest deliberately."
        }
    }
    if ($lockText -notmatch '(?m)^#.*Python 3\.12\.7\b') {
        throw 'requirements-lock.txt no longer records Python 3.12.7. Update and re-audit the source manifest deliberately.'
    }

    if (-not (Test-Path -LiteralPath $nativeReadmePath -PathType Leaf)) {
        throw "Native dependency record not found: $nativeReadmePath"
    }
    $nativeReadme = Get-Content -Raw -LiteralPath $nativeReadmePath -Encoding UTF8
    if ($nativeReadme -notmatch '\bMinHook\b.*\bv1\.3\.4\b') {
        throw 'native/README.md no longer records MinHook v1.3.4. Update and re-audit the source manifest deliberately.'
    }
}

function Assert-Archive {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [psobject]$ManifestRow
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required archive is missing: $Path"
    }
    $item = Get-Item -Force -LiteralPath $Path
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to verify a reparse-point archive: $Path"
    }
    if ($item.Length -ne $ManifestRow.ExpectedSize) {
        throw "Size mismatch for $($ManifestRow.Filename): expected $($ManifestRow.ExpectedSize), got $($item.Length)."
    }
    $actualHash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -cne $ManifestRow.Sha256) {
        throw "SHA-256 mismatch for $($ManifestRow.Filename): expected $($ManifestRow.Sha256), got $actualHash."
    }
}

function Save-VerifiedArchive {
    param(
        [Parameter(Mandatory = $true)]
        [System.Net.Http.HttpClient]$Client,

        [Parameter(Mandatory = $true)]
        [psobject]$ManifestRow,

        [Parameter(Mandatory = $true)]
        [string]$DestinationDirectory
    )

    $destinationPath = Join-Path $DestinationDirectory $ManifestRow.Filename
    if (Test-Path -LiteralPath $destinationPath) {
        Assert-Archive -Path $destinationPath -ManifestRow $ManifestRow
        Write-Host "Verified existing $($ManifestRow.Filename)"
        return
    }

    $partialName = '.partial-' + [guid]::NewGuid().ToString('N')
    $partialPath = Join-Path $DestinationDirectory $partialName
    $response = $null
    $inputStream = $null
    $outputStream = $null

    try {
        Write-Host "Downloading $($ManifestRow.Component) $($ManifestRow.Version) ($($ManifestRow.ExpectedSize) bytes)..."
        $response = $Client.GetAsync(
            $ManifestRow.SourceUri,
            [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead
        ).GetAwaiter().GetResult()
        [void]$response.EnsureSuccessStatusCode()

        $finalUri = $response.RequestMessage.RequestUri
        if ($null -eq $finalUri -or $finalUri.Scheme -cne 'https') {
            throw "Download for $($ManifestRow.Component) ended on a non-HTTPS URL."
        }
        $contentLength = $response.Content.Headers.ContentLength
        if ($null -ne $contentLength -and [long]$contentLength -ne $ManifestRow.ExpectedSize) {
            throw "Server length mismatch for $($ManifestRow.Filename): expected $($ManifestRow.ExpectedSize), got $contentLength."
        }

        $inputStream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $outputStream = [System.IO.File]::Open(
            $partialPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $buffer = New-Object byte[] (1024 * 1024)
        [long]$totalBytes = 0
        while (($bytesRead = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $totalBytes += $bytesRead
            if ($totalBytes -gt $ManifestRow.ExpectedSize) {
                throw "Download exceeded the expected size for $($ManifestRow.Filename)."
            }
            $outputStream.Write($buffer, 0, $bytesRead)
            $percent = [int](($totalBytes * 100) / $ManifestRow.ExpectedSize)
            Write-Progress -Activity "Downloading $($ManifestRow.Filename)" -Status "$totalBytes / $($ManifestRow.ExpectedSize) bytes" -PercentComplete $percent
        }
        Write-Progress -Activity "Downloading $($ManifestRow.Filename)" -Completed
        $outputStream.Flush()
        $outputStream.Dispose()
        $outputStream = $null
        $inputStream.Dispose()
        $inputStream = $null

        if ($totalBytes -ne $ManifestRow.ExpectedSize) {
            throw "Downloaded size mismatch for $($ManifestRow.Filename): expected $($ManifestRow.ExpectedSize), got $totalBytes."
        }
        $actualHash = (Get-FileHash -LiteralPath $partialPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -cne $ManifestRow.Sha256) {
            throw "SHA-256 mismatch for $($ManifestRow.Filename): expected $($ManifestRow.Sha256), got $actualHash."
        }

        Move-Item -LiteralPath $partialPath -Destination $destinationPath -ErrorAction Stop
        Assert-Archive -Path $destinationPath -ManifestRow $ManifestRow
        Write-Host "Saved and verified $($ManifestRow.Filename)"
    }
    finally {
        if ($null -ne $outputStream) {
            $outputStream.Dispose()
        }
        if ($null -ne $inputStream) {
            $inputStream.Dispose()
        }
        if ($null -ne $response) {
            $response.Dispose()
        }
        if (Test-Path -LiteralPath $partialPath -PathType Leaf) {
            $partialItem = Get-Item -Force -LiteralPath $partialPath
            if (($partialItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0) {
                Remove-Item -LiteralPath $partialPath -ErrorAction Stop
            }
        }
    }
}

function Write-ChecksumFile {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$ManifestRows,

        [Parameter(Mandatory = $true)]
        [string]$DestinationDirectory
    )

    $checksumPath = Join-Path $DestinationDirectory 'SOURCE_ARCHIVES.SHA256'
    $checksumLines = @(
        $ManifestRows | ForEach-Object { "$($_.Sha256)  $($_.Filename)" }
    )
    $desiredContent = ($checksumLines -join "`n") + "`n"

    if (Test-Path -LiteralPath $checksumPath) {
        $existingContent = Get-Content -Raw -LiteralPath $checksumPath -Encoding UTF8
        if ($existingContent.Replace("`r`n", "`n") -cne $desiredContent) {
            throw "Existing checksum file differs from the locked manifest: $checksumPath"
        }
        return
    }

    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    $stream = [System.IO.File]::Open(
        $checksumPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    try {
        $writer = New-Object System.IO.StreamWriter($stream, $utf8WithoutBom)
        try {
            $writer.Write($desiredContent)
            $writer.Flush()
        }
        finally {
            $writer.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

Assert-RepositoryBaselines
$manifestRows = @(Read-SourceManifest -Path $manifestPath)

if ($DryRun) {
    [long]$totalSize = 0
    Write-Host 'Manifest and repository baselines are valid. No network request or file write was made.'
    foreach ($row in $manifestRows) {
        $totalSize += $row.ExpectedSize
        Write-Host ("  {0,-8} {1,-8} {2,12:N0} bytes  {3}" -f $row.Component, $row.Version, $row.ExpectedSize, $row.Filename)
        if ($row.HashAuthority -ceq 'independent_pinned') {
            Write-Warning "$($row.Component) uses an independently recorded digest: $($row.HashEvidence)"
        }
    }
    Write-Host ("Total expected download: {0:N0} bytes ({1:N2} MiB)." -f $totalSize, ($totalSize / 1MB))
    Write-Host "Output directory for a real run: $outputFullPath"
    return
}

if (-not (Test-Path -LiteralPath $outputFullPath)) {
    [void](New-Item -ItemType Directory -Path $outputFullPath -ErrorAction Stop)
}
$outputItem = Get-Item -Force -LiteralPath $outputFullPath
if (-not $outputItem.PSIsContainer -or
    ($outputItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Output path must be a real directory, not a file or reparse point: $outputFullPath"
}

if ($VerifyOnly) {
    foreach ($row in $manifestRows) {
        Assert-Archive -Path (Join-Path $outputFullPath $row.Filename) -ManifestRow $row
        Write-Host "Verified $($row.Filename)"
    }
    Write-Host 'All locked third-party source archives passed size and SHA-256 verification.'
    return
}

Add-Type -AssemblyName System.Net.Http
$handler = New-Object System.Net.Http.HttpClientHandler
$handler.AllowAutoRedirect = $true
$handler.MaxAutomaticRedirections = 5
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromHours(6)
$client.DefaultRequestHeaders.UserAgent.ParseAdd("RenpyThiefPatch-source-prep/$Version")

try {
    foreach ($row in $manifestRows) {
        Save-VerifiedArchive -Client $client -ManifestRow $row -DestinationDirectory $outputFullPath
    }
}
finally {
    $client.Dispose()
}

Write-ChecksumFile -ManifestRows $manifestRows -DestinationDirectory $outputFullPath
Write-Host "All source archives are ready in: $outputFullPath"
Write-Host 'Upload every archive plus SOURCE_ARCHIVES.SHA256 to the matching GitHub Release.'
