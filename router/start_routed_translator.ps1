[CmdletBinding()]
param(
    [ValidateSet('echo', 'openai', 'youdao', 'baidu', 'microsoft')]
    [string]$Mode = 'openai',

    [string]$BaseUrl = $env:UPSTREAM_BASE_URL,

    [string]$Model = $env:UPSTREAM_MODEL,

    [string]$ApiKeyFile = '',

    [ValidateSet('openai', 'deepseek', 'siliconflow-qwen', 'hunyuan-mt')]
    [string]$PayloadProfile = 'openai',

    [ValidateSet('omit', 'enabled', 'disabled')]
    [string]$Thinking = 'omit',

    [ValidateSet('none', 'low', 'medium', 'high')]
    [string]$ReasoningEffort = 'none',

    [ValidateRange(1, 128)]
    [int]$BridgeConcurrency = 64,

    [ValidateRange(1, 128)]
    [int]$UpstreamConcurrency = 8,

    [ValidateRange(0, 1000000)]
    [int]$CacheEntries = 2048,

    [ValidateRange(0, 1073741824)]
    [long]$CacheBytes = 16777216,

    [string]$BridgeExecutable = '',

    [string]$TranslatorPath = '',

    [ValidateSet('true', 'false')]
    [string]$BlockUpdates = 'true',

    [ValidateRange(2, 60)]
    [int]$BridgeStartupTimeoutSec = 10,

    [ValidateRange(5, 60)]
    [int]$DynamicPortTimeoutSec = 20
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
# StrictMode treats $LASTEXITCODE as unset until a native command has run.
# Do not read $script:Name in this file: [CmdletBinding()] + StrictMode
# reports those as unset even after a top-level assignment.
$global:LASTEXITCODE = 0

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($TranslatorPath)) {
    $TranslatorPath = Join-Path $scriptDir '..\..\RenpyThief.exe'
}

function Write-StrictModeFailure {
    param($ErrorRecord)
    if ($null -eq $ErrorRecord) { return }
    $line = ''
    if ($null -ne $ErrorRecord.InvocationInfo) {
        $line = [string]$ErrorRecord.InvocationInfo.PositionMessage
    }
    $message = 'STRICT {0} {1} {2}' -f $ErrorRecord.FullyQualifiedErrorId, $ErrorRecord.Exception.Message, $line
    Write-Host $message
    $log = Join-Path $scriptDir 'strict_error.log'
    Add-Content -LiteralPath $log -Value $message -ErrorAction SilentlyContinue
}

trap {
    Write-StrictModeFailure -ErrorRecord $_
    break
}

function Resolve-DumpOnlyPython {
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    foreach ($candidate in @(
        (Join-Path $env:USERPROFILE 'anaconda3\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe')
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    return $null
}

$bridgePort = 19899
$routerDir = $scriptDir
$bridgePath = Join-Path $routerDir 'translate_bridge.py'
$routeDllSource = Join-Path $routerDir 'ipcroute.dll'
$injectRouteDllSource = Join-Path $routerDir 'injectroute.dll'
$injectorPath = Join-Path $routerDir 'netinject.exe'
$guardLauncherPath = Join-Path $routerDir 'guardlaunch.exe'
$versionGuardSource = Join-Path $routerDir 'versionguard.dll'
$versionGuardConfig = Join-Path $routerDir 'versionguard.ini'
$runtimeRoot = Join-Path $routerDir 'runtime'
$blockUpdatesEnabled = [bool]::Parse($BlockUpdates)

$bridgeProcess = $null
$translatorProcess = $null
$routeDllLoaded = $false
$routeActive = $false
$runtimeDir = $null
$resolvedApiKeyFile = $null

function Get-PortListeners {
    param([Parameter(Mandatory = $true)][int]$Port)

    $netstatPath = Join-Path $env:SystemRoot 'System32\netstat.exe'
    $lines = & $netstatPath -ano -p TCP
    $netstatExit = $global:LASTEXITCODE
    if ($netstatExit -ne 0) {
        throw "netstat failed while checking TCP port $Port (exit $netstatExit)."
    }

    $results = @()
    foreach ($line in $lines) {
        if ($line -notmatch '^\s*TCP\s+(?<local>\S+)\s+\S+\s+LISTENING\s+(?<owner>\d+)\s*$') {
            continue
        }
        $localEndpoint = [string]$Matches['local']
        $separator = $localEndpoint.LastIndexOf(':')
        if ($separator -lt 0) {
            continue
        }
        [int]$parsedPort = 0
        if (![int]::TryParse($localEndpoint.Substring($separator + 1), [ref]$parsedPort) -or
            $parsedPort -ne $Port) {
            continue
        }

        $addressText = $localEndpoint.Substring(0, $separator).Trim('[', ']')
        $parsedAddress = $null
        $isIpAddress = [Net.IPAddress]::TryParse($addressText, [ref]$parsedAddress)
        $isLoopback = $isIpAddress -and [Net.IPAddress]::IsLoopback($parsedAddress)
        $isWildcard = $addressText -eq '0.0.0.0' -or $addressText -eq '::'
        $results += [pscustomobject]@{
            Address = $addressText
            Port = $parsedPort
            OwnerPid = [int]$Matches['owner']
            IsLoopback = $isLoopback
            IsWildcard = $isWildcard
        }
    }
    return $results
}

function Test-PortInUse {
    param([Parameter(Mandatory = $true)][int]$Port)

    return @(
        [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
            Where-Object { $_.Port -eq $Port }
    ).Count -gt 0
}

function Test-BridgeHealth {
    param([Parameter(Mandatory = $true)][int]$Port)

    $client = New-Object Net.Sockets.TcpClient
    try {
        $pending = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (!$pending.AsyncWaitHandle.WaitOne(500)) {
            return $false
        }
        $client.EndConnect($pending)
        $client.ReceiveTimeout = 1000
        $client.SendTimeout = 1000
        $stream = $client.GetStream()
        $request = [Text.Encoding]::ASCII.GetBytes(
            "GET /health HTTP/1.0`r`nHost: 127.0.0.1:$Port`r`nConnection: close`r`n`r`n"
        )
        $stream.Write($request, 0, $request.Length)

        $buffer = New-Object byte[] 4096
        $response = New-Object IO.MemoryStream
        try {
            while (($count = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $response.Write($buffer, 0, $count)
                if ($response.Length -gt 65536) {
                    return $false
                }
            }
            $text = [Text.Encoding]::UTF8.GetString($response.ToArray())
            $parts = $text -split "`r`n`r`n", 2
            return $parts.Count -eq 2 -and
                   $parts[0] -match '^HTTP/1\.[01] 200(?:\s|$)' -and
                   $parts[1].Trim() -eq 'ok'
        } finally {
            $response.Dispose()
        }
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Assert-NoTranslatorProcess {
    $running = @(Get-Process -Name 'RenpyThief' -ErrorAction SilentlyContinue)
    if ($running.Count -gt 0) {
        $ids = ($running | ForEach-Object { $_.Id }) -join ', '
        throw "RenpyThief is already running (PID(s): $ids). Exit it normally, then rerun this launcher so routing is active before any game can translate."
    }
}

function Assert-BridgeListenerOwner {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    $listeners = @(Get-PortListeners -Port $bridgePort)
    $owned = @($listeners | Where-Object {
        $_.OwnerPid -eq $ProcessId -and $_.Address -eq '127.0.0.1'
    })
    if ($listeners.Count -ne 1 -or $owned.Count -ne 1) {
        $details = if ($listeners.Count -eq 0) {
            'no listener'
        } else {
            ($listeners | ForEach-Object {
                "$($_.Address):$($_.Port) PID $($_.OwnerPid)"
            }) -join '; '
        }
        throw "The bridge process does not exclusively own 127.0.0.1:$bridgePort ($details)."
    }
}

function Test-RenpyScriptBridgeEnabled {
    # 1.1.0：不写游戏脚本层。解袋走主进程桌上的 text+translateType。
    # Do not use $script: flags here: [CmdletBinding()] + StrictMode treats
    # $script:Name as unset even after a top-level assignment.
    return $false
}

function Test-FirstHopHijackEnabled {
    # 1.1.0：不要提前截走第一跳密文回声，让主进程自己解袋。
    return $false
}

function Stop-OwnedBridge {
    param($Process)
    if ($null -eq $Process) { return }
    if (!$Process.HasExited) {
        $Process.Kill()
        $Process.WaitForExit(5000) | Out-Null
    }
}

function Remove-SensitiveChildEnvironment {
    param([Parameter(Mandatory = $true)][Diagnostics.ProcessStartInfo]$Info)

    # The bridge has already inherited anything it needs. Do not let a closed-
    # source translator (or a game it launches) inherit unrelated API secrets or
    # custom prompts from the parent shell. Provider-specific credentials added
    # later are covered by both prefix and suffix matching.
    # Also drop packaged PyQt5 plugin paths. The 64-bit patch GUI sets QT_* for
    # itself; 32-bit RenpyThief must use the plugins next to its own exe.
    foreach ($name in @($Info.EnvironmentVariables.Keys)) {
        $upper = $name.ToString().ToUpperInvariant()
        if ($upper -eq 'BRIDGE_LOG_CONTENT' -or
            $upper -eq 'QTDIR' -or
            $upper.StartsWith('QT_') -or
            $upper.StartsWith('QTWEBENGINE') -or
            $upper.StartsWith('QML') -or
            $upper.StartsWith('UPSTREAM_') -or
            $upper.StartsWith('OPENAI_') -or
            $upper.StartsWith('DEEPSEEK_') -or
            $upper.StartsWith('SILICONFLOW_') -or
            $upper.StartsWith('BAIDU_') -or
            $upper.StartsWith('YOUDAO_') -or
            $upper.StartsWith('MICROSOFT_TRANSLATOR_') -or
            $upper.EndsWith('_API_KEY') -or
            $upper.EndsWith('_SECRET') -or
            $upper.EndsWith('_TOKEN') -or
            $upper.EndsWith('_ACCESS_KEY')) {
            $Info.EnvironmentVariables.Remove($name.ToString())
        }
    }
}

function ConvertFrom-QtIniValue {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $Value }
    return [regex]::Replace($Value, '\\x([0-9A-Fa-f]{4})', {
        param($match)
        [string][char][int]('0x' + $match.Groups[1].Value)
    })
}

function Read-LastInjectPath {
    param([string]$SettingsPath)
    if (-not (Test-Path -LiteralPath $SettingsPath -PathType Leaf)) { return '' }
    foreach ($line in Get-Content -LiteralPath $SettingsPath -Encoding UTF8) {
        if ($line -match '^lastInjectPath=(.*)$') {
            return ConvertFrom-QtIniValue -Value $Matches[1].Trim()
        }
    }
    return ''
}

function Test-RenpyGameRoot {
    param([string]$GameRoot)
    if ([string]::IsNullOrWhiteSpace($GameRoot)) { return $false }
    if (-not (Test-Path -LiteralPath $GameRoot -PathType Container)) { return $false }
    $gameDir = Join-Path $GameRoot 'game'
    if (-not (Test-Path -LiteralPath $gameDir -PathType Container)) { return $false }
    return @(Get-ChildItem -LiteralPath $gameDir -Filter '*.rpy' -File -ErrorAction SilentlyContinue).Count -gt 0
}

function Install-UnofficialRenpyBridge {
    param([string]$GameRoot)
    $source = Join-Path $routerDir '00unofficial_bridge.rpy'
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { return $false }
    if (-not (Test-RenpyGameRoot -GameRoot $GameRoot)) { return $false }
    $dest = Join-Path $GameRoot 'game\00unofficial_bridge.rpy'
    Copy-Item -LiteralPath $source -Destination $dest -Force
    return $true
}

function Remove-UnofficialRenpyBridge {
    param([string]$GameRoot)
    if ([string]::IsNullOrWhiteSpace($GameRoot)) { return $false }
    $dest = Join-Path $GameRoot 'game\00unofficial_bridge.rpy'
    if (-not (Test-Path -LiteralPath $dest -PathType Leaf)) { return $false }
    Remove-Item -LiteralPath $dest -Force
    return $true
}

function Test-PathUnderRoot {
    param([string]$Root, [string]$Candidate)
    if ([string]::IsNullOrWhiteSpace($Root) -or [string]::IsNullOrWhiteSpace($Candidate)) {
        return $false
    }
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $childFull = [IO.Path]::GetFullPath($Candidate)
    return $childFull.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)
}

function Sync-InjectorPlaintextRoute {
    param(
        [string]$OriginRoot,
        [string]$RuntimeDll,
        [string]$NetInject,
        [hashtable]$Seen
    )
    if ($null -eq $Seen) { return }
    if (!(Test-Path -LiteralPath $RuntimeDll -PathType Leaf)) { return }
    foreach ($name in @('RenpyInjector-x86.exe', 'RenpyInjector-x64.exe')) {
        $candidates = @(
            Get-Process -Name ($name.Substring(0, $name.Length - 4)) -ErrorAction SilentlyContinue |
                Where-Object { $null -ne $_ }
        )
        foreach ($process in $candidates) {
            $pidValue = [int]$process.Id
            if ($Seen.ContainsKey($pidValue)) { continue }
            $exePath = ''
            try { $exePath = [string]$process.Path } catch { $exePath = '' }
            if (-not (Test-PathUnderRoot -Root $OriginRoot -Candidate $exePath)) { continue }
            if ($name -eq 'RenpyInjector-x64.exe') {
                Write-Host "Skipping 64-bit injector PID $pidValue; injectroute is x86-only in this build."
                $Seen[$pidValue] = 'skipped-x64'
                continue
            }
            $savedErrorActionPreference = $ErrorActionPreference
            $output = @()
            $injectExitCode = 0
            try {
                $ErrorActionPreference = 'Continue'
                $output = @(& $NetInject "$pidValue" $RuntimeDll 2>&1)
                $injectExitCode = $global:LASTEXITCODE
            } finally {
                $ErrorActionPreference = $savedErrorActionPreference
            }
            if ($injectExitCode -ne 0) {
                $text = ($output | ForEach-Object { $_.ToString() }) -join ' '
                Write-Warning "injectroute attach failed for PID ${pidValue}: $text"
                $Seen[$pidValue] = 'failed'
                continue
            }
            Write-Host "Routed generic injector plaintext: $name PID $pidValue -> Bridge."
            $Seen[$pidValue] = 'injected'
        }
    }
}

function Clear-BridgeOnlyEnvironment {
    # Start-Process has already copied these values into the bridge. Remove the
    # supervisor's copies before it launches RenpyThief or helper processes.
    foreach ($name in @(
        'UPSTREAM_API_KEY',
        'UPSTREAM_CREDENTIALS_JSON',
        'UPSTREAM_PROMPT_MODE',
        'UPSTREAM_CUSTOM_PROMPT'
    )) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
}

function Start-IsolatedTranslator {
    param([Parameter(Mandatory = $true)][string]$Path)

    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $Path
    $startInfo.WorkingDirectory = Split-Path -Parent $Path
    $startInfo.UseShellExecute = $false
    Remove-SensitiveChildEnvironment -Info $startInfo
    $process = [Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) {
        throw 'RenpyThief did not return a process handle after launch.'
    }
    return $process
}

function Start-GuardedTranslator {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$GuardDll
    )

    if ($Path.Contains('"') -or $GuardDll.Contains('"')) {
        throw 'Guarded-launch paths cannot contain a double quote.'
    }
    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $guardLauncherPath
    $startInfo.Arguments = ('"{0}" "{1}"' -f $Path, $GuardDll)
    $startInfo.WorkingDirectory = $routerDir
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    Remove-SensitiveChildEnvironment -Info $startInfo

    $launcher = [Diagnostics.Process]::Start($startInfo)
    if ($null -eq $launcher) {
        throw 'guardlaunch did not return a process handle.'
    }
    # guardlaunch has bounded injection and readiness waits. It exits only after
    # resuming a protected child, or after terminating the still-suspended child.
    $stdout = $launcher.StandardOutput.ReadToEnd()
    $stderr = $launcher.StandardError.ReadToEnd()
    $launcher.WaitForExit()
    if ($launcher.ExitCode -ne 0) {
        $details = ($stderr + ' ' + $stdout).Trim()
        throw "Version-update guard failed closed (exit $($launcher.ExitCode)): $details"
    }
    [int]$processId = 0
    $pidHint = [regex]::Match($stdout, 'RenpyThief PID (\d+)')
    if ($pidHint.Success) {
        [int]::TryParse($pidHint.Groups[1].Value, [ref]$processId) | Out-Null
    }
    try {
        $match = [regex]::Match(
            $stdout.Trim(), '^Started guarded RenpyThief PID (\d+)\.$'
        )
        if (!$match.Success) {
            throw "Version-update guard returned an invalid readiness message: $stdout"
        }
        [int]$validatedProcessId = 0
        if (![int]::TryParse($match.Groups[1].Value,
                [ref]$validatedProcessId) -or
            $validatedProcessId -le 0 -or
            $validatedProcessId -ne $processId) {
            throw 'Version-update guard returned an invalid process ID.'
        }
        $guardWarning = $stderr.Trim()
        $expectedWarning = '^WARNING: no known version check was observed within (\d+) ms; continuing with update protection unconfirmed\.$'
        $warningMatch = [regex]::Match($guardWarning, $expectedWarning)
        if ($guardWarning -and !$warningMatch.Success) {
            throw "Version-update guard returned an unknown diagnostic: $guardWarning"
        }
        $process = Get-Process -Id $processId -ErrorAction Stop
        if ($guardWarning) {
            Write-Warning "UPDATE_GUARD_WARNING: timeout_ms=$($warningMatch.Groups[1].Value)"
        } else {
            Write-Host "Version-update guard confirmed a blocked version check for RenpyThief PID $processId."
        }
        return $process
    } catch {
        # A zero-exit guardlaunch has resumed a protected process. If protocol
        # validation fails after that point, close only the exact launcher-owned
        # RenpyThief path instead of leaving an untracked window behind.
        if ($processId -gt 0) {
            $candidate = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if ($null -ne $candidate -and
                [string]::Equals($candidate.Path, $Path,
                    [StringComparison]::OrdinalIgnoreCase)) {
                $candidate.Kill()
                $candidate.WaitForExit(5000) | Out-Null
            }
        }
        throw
    }
}

function Stop-UnroutedTranslator {
    param([Parameter(Mandatory = $true)][Diagnostics.Process]$Process)

    $Process.Refresh()
    if ($Process.HasExited) {
        return
    }
    Write-Warning "Route activation failed; closing launcher-owned RenpyThief PID $($Process.Id) so it cannot translate through the original service."
    $closeRequested = $false
    try {
        $closeRequested = $Process.CloseMainWindow()
    } catch [InvalidOperationException] {
        $closeRequested = $false
    }
    if ($closeRequested -and $Process.WaitForExit(5000)) {
        return
    }
    $Process.Refresh()
    if (!$Process.HasExited) {
        $Process.Kill()
        if (!$Process.WaitForExit(5000)) {
            throw "Could not terminate unrouted RenpyThief PID $($Process.Id). Do not use that window."
        }
    }
}

function New-RoutedBridgeArgumentList {
    param(
        [Parameter(Mandatory = $true)][string]$Program,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$BridgeMode,
        [Parameter(Mandatory = $true)][string]$BridgePayloadProfile,
        [Parameter(Mandatory = $true)][string]$BridgeThinking,
        [Parameter(Mandatory = $true)][string]$BridgeReasoningEffort,
        [Parameter(Mandatory = $true)][string]$BridgeMaxConcurrency,
        [Parameter(Mandatory = $true)][string]$BridgeUpstreamConcurrency,
        [Parameter(Mandatory = $true)][string]$BridgeCacheEntries,
        [Parameter(Mandatory = $true)][string]$BridgeCacheBytes,
        [string]$PythonScript = '',
        [string]$BridgeBaseUrl = '',
        [string]$BridgeModel = ''
    )

    $items = New-Object 'System.Collections.Generic.List[string]'
    if (-not [string]::IsNullOrWhiteSpace($PythonScript)) {
        $items.Add(('"{0}"' -f $PythonScript))
    }
    $parts = New-Object 'System.Collections.Generic.List[string]'
    foreach ($part in @(
        '--host', '127.0.0.1',
        '--port', '19899',
        '--mode', $BridgeMode,
        '--payload-profile', $BridgePayloadProfile,
        '--thinking', $BridgeThinking,
        '--reasoning-effort', $BridgeReasoningEffort,
        '--max-concurrency', $BridgeMaxConcurrency,
        '--upstream-concurrency', $BridgeUpstreamConcurrency,
        '--cache-entries', $BridgeCacheEntries,
        '--cache-bytes', $BridgeCacheBytes,
        '--log-path', ('"{0}"' -f $LogPath),
        '--no-log-content'
    )) {
        $parts.Add($part)
    }
    if (-not [string]::IsNullOrWhiteSpace($BridgeBaseUrl)) {
        $parts.Add('--base-url')
        $parts.Add(('{0}' -f $BridgeBaseUrl))
    }
    if (-not [string]::IsNullOrWhiteSpace($BridgeModel)) {
        $parts.Add('--model')
        $parts.Add($BridgeModel)
    }
    foreach ($part in $parts) {
        if ([string]::IsNullOrWhiteSpace($part)) {
            throw 'Routed bridge argument list contained an empty value.'
        }
        $items.Add($part)
    }
    if ([string]::IsNullOrWhiteSpace($Program)) {
        throw 'Routed bridge program was empty.'
    }
    return $items.ToArray()
}

try {
    Write-Host "1.1.0 hub route: first-hop hijack off; bridge Mode=$Mode payload=$PayloadProfile."

    $requiredFiles = @($routeDllSource, $injectRouteDllSource, $injectorPath)
    if ($blockUpdatesEnabled) {
        $requiredFiles += @($guardLauncherPath, $versionGuardSource, $versionGuardConfig)
    }
    foreach ($requiredFile in $requiredFiles) {
        if (!(Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw "Required file not found: $requiredFile"
        }
    }

    $bridgeProgram = $null
    $pythonScriptForBridge = ''
    $pythonPath = Resolve-DumpOnlyPython
    if ((Test-Path -LiteralPath $bridgePath -PathType Leaf) -and (-not [string]::IsNullOrWhiteSpace($pythonPath))) {
        # Frozen GUI still passes -BridgeExecutable translate_bridge.exe.
        # That exe can lag this script and miss URL text= / official echo.
        $bridgeProgram = $pythonPath
        $pythonScriptForBridge = $bridgePath
        Write-Host '1.1.0: using Python translate_bridge.py (ignore packaged exe so URL text= is read).'
    } elseif (-not [string]::IsNullOrWhiteSpace($BridgeExecutable)) {
        $resolvedBridgePath = Resolve-Path -LiteralPath $BridgeExecutable -ErrorAction Stop
        if ($resolvedBridgePath.Provider.Name -ne 'FileSystem') {
            throw 'BridgeExecutable must use the FileSystem provider.'
        }
        $bridgeItem = Get-Item -LiteralPath $resolvedBridgePath.ProviderPath -Force -ErrorAction Stop
        if (!($bridgeItem -is [IO.FileInfo]) -or
            ($bridgeItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            $bridgeItem.Extension -ne '.exe') {
            throw 'BridgeExecutable must be a regular .exe file, not a directory or reparse point.'
        }
        $bridgeProgram = $bridgeItem.FullName
        Write-Host '1.1.0: Python unavailable; falling back to packaged translate_bridge.exe.'
    } else {
        throw "Required file not found: $bridgePath"
    }

    Assert-NoTranslatorProcess
    $resolvedTranslator = (Resolve-Path -LiteralPath $TranslatorPath -ErrorAction Stop).Path
    if (Test-PortInUse -Port $bridgePort) {
        throw "TCP port 127.0.0.1:$bridgePort is already in use. Stop the existing service or choose a clean session; the fixed ipcroute bridge port cannot be shared."
    }

    $bridgeLogPath = Join-Path $routerDir 'bridge_requests.log'
    $bridgeArgs = New-RoutedBridgeArgumentList -Program $bridgeProgram `
        -LogPath $bridgeLogPath `
        -BridgeMode $Mode `
        -BridgePayloadProfile $PayloadProfile `
        -BridgeThinking $Thinking `
        -BridgeReasoningEffort $ReasoningEffort `
        -BridgeMaxConcurrency ([string]$BridgeConcurrency) `
        -BridgeUpstreamConcurrency ([string]$UpstreamConcurrency) `
        -BridgeCacheEntries ([string]$CacheEntries) `
        -BridgeCacheBytes ([string]$CacheBytes) `
        -PythonScript $pythonScriptForBridge `
        -BridgeBaseUrl ([string]$BaseUrl) `
        -BridgeModel ([string]$Model)
    $bridgeProcess = Start-Process -FilePath $bridgeProgram -ArgumentList $bridgeArgs `
        -WorkingDirectory $routerDir -WindowStyle Hidden -PassThru
    Clear-BridgeOnlyEnvironment

    $bridgeDeadline = [DateTime]::UtcNow.AddSeconds($BridgeStartupTimeoutSec)
    $bridgeReady = $false
    while ([DateTime]::UtcNow -lt $bridgeDeadline) {
        if ($bridgeProcess.HasExited) {
            throw "Translation bridge exited during startup with code $($bridgeProcess.ExitCode)."
        }
        if (Test-BridgeHealth -Port $bridgePort) {
            $bridgeReady = $true
            break
        }
        Start-Sleep -Milliseconds 150
    }
    if (!$bridgeReady) {
        throw "Translation bridge did not become healthy on 127.0.0.1:$bridgePort within $BridgeStartupTimeoutSec seconds."
    }
    Assert-BridgeListenerOwner -ProcessId $bridgeProcess.Id

    New-Item -ItemType Directory -Path $runtimeRoot -ErrorAction SilentlyContinue | Out-Null
    $runtimeName = 'launch-{0}' -f (Get-Date -Format 'yyyyMMdd-HHmmssfff')
    $runtimeDir = Join-Path $runtimeRoot $runtimeName
    New-Item -ItemType Directory -Path $runtimeDir -ErrorAction Stop | Out-Null

    $guardDll = $versionGuardSource
    if ($blockUpdatesEnabled) {
        $runtimeGuardDll = Join-Path $runtimeDir 'versionguard.dll'
        $runtimeGuardIni = Join-Path $runtimeDir 'versionguard.ini'
        Copy-Item -LiteralPath $versionGuardSource -Destination $runtimeGuardDll -ErrorAction Stop
        [IO.File]::WriteAllText(
            $runtimeGuardIni,
            "[versionguard]`r`nmode=lock`r`nlocal_version=auto`r`nsession_compat=lock`r`nconfig_compat=deny`r`ntranslate_compat=lock`r`n",
            (New-Object Text.UTF8Encoding($false))
        )
        if ((Get-FileHash -LiteralPath $versionGuardSource -Algorithm SHA256).Hash -ne
            (Get-FileHash -LiteralPath $runtimeGuardDll -Algorithm SHA256).Hash) {
            throw "Runtime versionguard hash verification failed: $runtimeGuardDll"
        }
        $guardDll = $runtimeGuardDll
        $translatorProcess = Start-GuardedTranslator -Path $resolvedTranslator `
            -GuardDll $guardDll
    } else {
        $translatorProcess = Start-IsolatedTranslator -Path $resolvedTranslator
    }
    $actualTranslatorPath = (Get-Process -Id $translatorProcess.Id -ErrorAction Stop).Path
    if (![string]::Equals($actualTranslatorPath, $resolvedTranslator,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "Started PID $($translatorProcess.Id) from an unexpected path: $actualTranslatorPath"
    }
    try {
        if (!$translatorProcess.WaitForInputIdle(15000)) {
            throw 'RenpyThief did not reach an input-ready GUI state within 15 seconds.'
        }
    } catch [InvalidOperationException] {
        throw 'RenpyThief exited before reaching an input-ready GUI state.'
    }
    Write-Host "Started clean RenpyThief PID $($translatorProcess.Id)."

    if (!(Test-BridgeHealth -Port $bridgePort)) {
        throw 'Translation bridge became unhealthy before injection.'
    }
    Assert-BridgeListenerOwner -ProcessId $bridgeProcess.Id

    $runtimeDll = Join-Path $runtimeDir 'ipcroute.dll'
    $runtimeInjectRouteDll = Join-Path $runtimeDir 'injectroute.dll'
    $runtimeIni = Join-Path $runtimeDir 'ipcroute.ini'
    $routeLog = Join-Path $runtimeDir 'ipcroute.log'

    if (Test-FirstHopHijackEnabled) {
        Copy-Item -LiteralPath $routeDllSource -Destination $runtimeDll -ErrorAction Stop
        Copy-Item -LiteralPath $injectRouteDllSource -Destination $runtimeInjectRouteDll -ErrorAction Stop
        [IO.File]::WriteAllText(
            $runtimeIni,
            "[ipcroute]`r`nmode=hijack`r`n",
            (New-Object Text.UTF8Encoding($false))
        )
        if ((Get-FileHash -LiteralPath $routeDllSource -Algorithm SHA256).Hash -ne
            (Get-FileHash -LiteralPath $runtimeDll -Algorithm SHA256).Hash) {
            throw "Runtime DLL hash verification failed: $runtimeDll"
        }

        if (Test-Path -LiteralPath $routeLog) {
            throw "New runtime unexpectedly contains an old readiness log: $routeLog"
        }

        # Windows PowerShell turns native stderr into an ErrorRecord. Capture it so
        # guarded injector refusals retain their precise message and exit code.
        $savedErrorActionPreference = $ErrorActionPreference
        $injectOutput = @()
        $injectExitCode = 0
        try {
            $ErrorActionPreference = 'Continue'
            $injectOutput = @(& $injectorPath "$($translatorProcess.Id)" $runtimeDll 2>&1)
            $injectExitCode = $global:LASTEXITCODE
        } finally {
            $ErrorActionPreference = $savedErrorActionPreference
        }
        if ($injectExitCode -ne 0) {
            $injectText = ($injectOutput | ForEach-Object { $_.ToString() }) -join ' '
            throw "netinject failed with exit code ${injectExitCode}: $injectText"
        }
        $routeDllLoaded = $true

        $hookDeadline = [DateTime]::UtcNow.AddSeconds(5)
        $hookReady = $false
        while ([DateTime]::UtcNow -lt $hookDeadline) {
            if (Test-Path -LiteralPath $routeLog -PathType Leaf) {
                $logText = [string](Get-Content -LiteralPath $routeLog -Raw)
                if ($logText -match 'configuration mode=hijack' -and
                    $logText -match 'hook enabled status=0') {
                    $hookReady = $true
                    break
                }
                if ($logText -match 'initialization failed|hook create failed|hook enabled status=(?!0)\d+') {
                    throw "ipcroute loaded but its WSAAccept hook failed. Restart RenpyThief before retrying. Log: $routeLog"
                }
            }
            Start-Sleep -Milliseconds 100
        }
        if (!$hookReady) {
            throw "ipcroute DLL loaded but did not confirm hook readiness within 5 seconds. Restart RenpyThief before retrying. Log: $routeLog"
        }

        # RenpyThief creates three consecutive loopback listeners. Do not announce
        # readiness (and therefore do not invite a game launch) until the DLL has
        # observed all three and selected the lowest translation port.
        $baseDeadline = [DateTime]::UtcNow.AddSeconds($DynamicPortTimeoutSec)
        $dynamicBase = 0
        while ([DateTime]::UtcNow -lt $baseDeadline) {
            if (!(Get-Process -Id $translatorProcess.Id -ErrorAction SilentlyContinue)) {
                throw 'RenpyThief exited before publishing its dynamic translation port.'
            }
            if ($bridgeProcess.HasExited) {
                throw "Translation bridge exited before dynamic-port discovery with code $($bridgeProcess.ExitCode)."
            }
            $logText = [string](Get-Content -LiteralPath $routeLog -Raw)
            $baseMatch = [regex]::Match($logText, 'dynamic_base=(\d+) listeners=')
            if ($baseMatch.Success -and
                [int]::TryParse($baseMatch.Groups[1].Value, [ref]$dynamicBase) -and
                $dynamicBase -ge 1 -and $dynamicBase -le 65533) {
                break
            }
            $dynamicBase = 0
            Start-Sleep -Milliseconds 100
        }
        if ($dynamicBase -eq 0) {
            throw "ipcroute did not discover RenpyThief's dynamic three-port group within $DynamicPortTimeoutSec seconds. Do not drag a game; restart RenpyThief before retrying. Log: $routeLog"
        }

        if ($bridgeProcess.HasExited) {
            throw "Translation bridge exited immediately before route activation with code $($bridgeProcess.ExitCode)."
        }
        Assert-BridgeListenerOwner -ProcessId $bridgeProcess.Id
        $routeActive = $true
        Write-Host "Translator-wide route active: RenpyThief PID $($translatorProcess.Id), dynamic loopback base $dynamicBase -> 127.0.0.1:$bridgePort ($Mode)."
    } else {
        if ($bridgeProcess.HasExited) {
            throw "Translation bridge exited immediately before route activation with code $($bridgeProcess.ExitCode)."
        }
        Assert-BridgeListenerOwner -ProcessId $bridgeProcess.Id
        $routeActive = $true
        Write-Host "Translator-wide route active: RenpyThief PID $($translatorProcess.Id), hub route (no first-hop hijack) -> 127.0.0.1:$bridgePort ($Mode)."
    }
    Write-Host "Runtime: $runtimeDir"
    Write-Host 'No game configuration was read or changed. Keep this launcher running while translating.'

    $settingsPath = Join-Path ([IO.Path]::GetDirectoryName($resolvedTranslator)) 'settings.ini'
    $originRoot = [IO.Path]::GetDirectoryName($resolvedTranslator)
    $lastBridgeGame = ''
    $injectorRouteSeen = @{}
    while (Get-Process -Id $translatorProcess.Id -ErrorAction SilentlyContinue) {
        if ($bridgeProcess.HasExited) {
            throw "Translation bridge exited unexpectedly with code $($bridgeProcess.ExitCode). RenpyThief must be restarted before retrying."
        }
        try {
            $droppedGame = Read-LastInjectPath -SettingsPath $settingsPath
            if ($droppedGame -and $droppedGame -ne $lastBridgeGame) {
                if (Test-RenpyScriptBridgeEnabled) {
                    if (Install-UnofficialRenpyBridge -GameRoot $droppedGame) {
                        Write-Host "Installed unofficial Ren'Py bridge script into the dropped game."
                    }
                } elseif (Remove-UnofficialRenpyBridge -GameRoot $droppedGame) {
                    Write-Host "Ren'Py script bridge disabled; removed leftover 00unofficial_bridge.rpy."
                } else {
                    Write-Host "Ren'Py script bridge disabled; not installing 00unofficial_bridge.rpy."
                }
                $lastBridgeGame = $droppedGame
            }
            if (Test-FirstHopHijackEnabled) {
                Sync-InjectorPlaintextRoute -OriginRoot $originRoot `
                    -RuntimeDll $runtimeInjectRouteDll -NetInject $injectorPath `
                    -Seen $injectorRouteSeen
            }
        } catch {
            Write-StrictModeFailure -ErrorRecord $_
            Write-Warning ("Route maintenance failed: {0} {1}" -f $_.FullyQualifiedErrorId, $_.Exception.Message)
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Host 'RenpyThief exited; stopping the bridge started by this launcher.'
} finally {
    $translatorStillRunning = $false
    if ($null -ne $translatorProcess) {
        $translatorStillRunning = $null -ne (Get-Process -Id $translatorProcess.Id -ErrorAction SilentlyContinue)
    }

    if ($translatorStillRunning -and !$routeActive) {
        Stop-UnroutedTranslator -Process $translatorProcess
        $translatorStillRunning = $null -ne (Get-Process -Id $translatorProcess.Id -ErrorAction SilentlyContinue)
    }

    if ($routeActive -and $translatorStillRunning) {
        if ($null -ne $bridgeProcess -and !$bridgeProcess.HasExited) {
            Write-Warning "An active routed RenpyThief still contains ipcroute.dll, so bridge PID $($bridgeProcess.Id) was left running to avoid breaking translations. Exit RenpyThief before stopping that bridge."
        }
    } elseif ($translatorStillRunning -and $routeDllLoaded) {
        # Stop-UnroutedTranslator normally makes this unreachable. If Windows
        # refused termination, retaining the bridge is safer for the loaded hook.
        Write-Warning "Unrouted RenpyThief PID $($translatorProcess.Id) could not be closed; its bridge was left running. Do not use that window."
    } else {
        Stop-OwnedBridge -Process $bridgeProcess
    }
}
