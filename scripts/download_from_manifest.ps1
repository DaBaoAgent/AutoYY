[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Manifest,
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,
    [string]$YtDlp = "yt-dlp",
    [string]$FfmpegLocation,
    [string]$CookiesFromBrowser,
    [string]$Proxy,
    [ValidateRange(144, 4320)]
    [int]$MinHeight = 720,
    [ValidateRange(144, 4320)]
    [int]$MaxHeight = 1080,
    [switch]$PlanOnly,
    # ---- P1 工程化新增参数 ----
    [ValidateRange(1, 8)]
    [int]$Parallel = 1,
    [switch]$UseAria2,
    [ValidateRange(1, 16)]
    [int]$Aria2Connections = 8,
    [switch]$Trace,
    # yt-dlp 2026+ 解析 YouTube 需要 JS runtime；留空则自动探测 node/deno
    [string]$JsRuntimes
)

$ErrorActionPreference = "Stop"
$StatusCsvName = "下载状态.csv"

function Get-DetectedJsRuntimes {
    param([string]$Requested)
    if (-not [string]::IsNullOrWhiteSpace($Requested)) { return $Requested }
    if (Get-Command node -ErrorAction SilentlyContinue) { return "node" }
    if (Get-Command deno -ErrorAction SilentlyContinue) { return "deno" }
    return $null
}

function Get-DetectedFfmpegLocation {
    param([string]$Requested)
    if (-not [string]::IsNullOrWhiteSpace($Requested)) { return $Requested }
    $cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($cmd) { return Split-Path -Parent $cmd.Source }
    # 常见用户目录：C:\Users\<user>\ffmpeg\*\bin
    $userFfmpeg = Join-Path $env:USERPROFILE "ffmpeg"
    if (Test-Path -LiteralPath $userFfmpeg) {
        $candidates = @(
            Get-ChildItem -LiteralPath $userFfmpeg -Directory -ErrorAction SilentlyContinue |
                ForEach-Object { Join-Path $_.FullName "bin" } |
                Where-Object { Test-Path -LiteralPath (Join-Path $_ "ffmpeg.exe") }
        )
        if ($candidates.Count -gt 0) { return $candidates[0] }
    }
    return $null
}

function Add-JsRuntimeArgument {
    param(
        [System.Collections.Generic.List[string]]$Arguments,
        [string]$JsRuntimes
    )
    if (-not [string]::IsNullOrWhiteSpace($JsRuntimes)) {
        $Arguments.Add("--js-runtimes")
        $Arguments.Add($JsRuntimes)
    }
}

function Add-OptionalArgument {
    param(
        [System.Collections.Generic.List[string]]$Arguments,
        [string]$Name,
        [string]$Value
    )
    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        $Arguments.Add($Name)
        $Arguments.Add($Value)
    }
}

function Get-Aria2cPath {
    $candidates = @()
    $cmd = Get-Command aria2c -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    $candidates += @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\aria2c.exe"),
        (Join-Path $env:USERPROFILE "aria2\aria2c.exe"),
        "C:\Program Files\aria2\aria2c.exe"
    )
    foreach ($cand in $candidates) {
        if ($cand -and (Test-Path -LiteralPath $cand)) { return $cand }
    }
    return $null
}

function Get-SubtitleLanguage {
    param([string]$Url, [hashtable]$Ctx)

    $arguments = [System.Collections.Generic.List[string]]::new()
    @("--dump-single-json", "--skip-download", "--no-playlist", "--no-warnings", $Url) |
        ForEach-Object { $arguments.Add($_) }
    Add-OptionalArgument $arguments "--cookies-from-browser" $Ctx.CookiesFromBrowser
    Add-OptionalArgument $arguments "--proxy" $Ctx.Proxy
    Add-JsRuntimeArgument $arguments $Ctx.JsRuntimes

    $jsonText = (& $Ctx.YtDlp @arguments) -join "`n"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($jsonText)) {
        return $null
    }
    try {
        $metadata = $jsonText | ConvertFrom-Json
    } catch {
        return $null
    }

    $manual = @()
    $automatic = @()
    if ($metadata.subtitles) {
        $manual = @($metadata.subtitles.PSObject.Properties.Name | Where-Object { $_ -ne "live_chat" })
    }
    if ($metadata.automatic_captions) {
        $automatic = @($metadata.automatic_captions.PSObject.Properties.Name | Where-Object { $_ -ne "live_chat" })
    }

    $groups = @(
        @($manual | Where-Object { $_ -eq "en" }),
        @($manual | Where-Object { $_ -like "en*" }),
        @($automatic | Where-Object { $_ -eq "en" }),
        @($automatic | Where-Object { $_ -like "en*" }),
        @($manual | Where-Object { $_ -like "zh*" }),
        @($automatic | Where-Object { $_ -like "zh*" }),
        $manual,
        $automatic
    )
    foreach ($group in $groups) {
        $candidate = $group | Select-Object -First 1
        if ($candidate) {
            return $candidate
        }
    }
    return $null
}

# 单个选题的处理逻辑（顺序/并行共用）。返回 [pscustomobject] 结果行。
$processRowScript = {
    param($row, $ctx)

    function Add-OptionalArgument {
        param(
            [System.Collections.Generic.List[string]]$Arguments,
            [string]$Name,
            [string]$Value
        )
        if (-not [string]::IsNullOrWhiteSpace($Value)) {
            $Arguments.Add($Name)
            $Arguments.Add($Value)
        }
    }

    function Invoke-YtDlp {
        param([string[]]$Arguments, [hashtable]$Ctx)
        if ($Ctx.Trace) {
            Write-Output "TRACE> $($Ctx.YtDlp) $($Arguments -join ' ')"
        }
        & $Ctx.YtDlp @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "yt-dlp failed with exit code $LASTEXITCODE"
        }
    }

    $folderName = [string]$row.folder_name
    $url = [string]$row.url
    $result = [pscustomobject]@{
        folder_name = $folderName
        url = $url
        video = $false
        subtitle = $false
        subtitle_language = ""
        attempts = 1
        status = "pending"
    }
    if ([string]::IsNullOrWhiteSpace($folderName) -or [string]::IsNullOrWhiteSpace($url)) {
        $result.status = "invalid manifest row"
        return $result
    }
    if ($url -notmatch '^https://(www\.)?(youtube\.com/watch\?|youtu\.be/)') {
        $result.status = "unsupported URL"
        return $result
    }

    $topicDir = Join-Path $ctx.OutputRoot $folderName
    New-Item -ItemType Directory -Path $topicDir -Force | Out-Null

    $existingVideo = Get-ChildItem -LiteralPath $topicDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.BaseName -eq "高清源视频" -and $_.Extension -in @(".mp4", ".mkv", ".webm") } |
        Select-Object -First 1
    $status = "complete"
    $subtitleLanguage = [string]$row.subtitle_language

    try {
        if (-not $existingVideo) {
            $arguments = [System.Collections.Generic.List[string]]::new()
            @(
                "--continue", "--no-overwrites", "--no-playlist",
                "--retries", "10", "--fragment-retries", "10",
                "--concurrent-fragments", "8",
                "--embed-metadata",
                "--merge-output-format", "mp4", "--remux-video", "mp4",
                "-f", "bestvideo[height<=$($ctx.MaxHeight)][height>=$($ctx.MinHeight)]+bestaudio/best[height<=$($ctx.MaxHeight)][height>=$($ctx.MinHeight)]",
                "-o", (Join-Path $topicDir "高清源视频.%(ext)s"), $url
            ) | ForEach-Object { $arguments.Add($_) }
            Add-OptionalArgument $arguments "--ffmpeg-location" $ctx.FfmpegLocation
            Add-OptionalArgument $arguments "--cookies-from-browser" $ctx.CookiesFromBrowser
            Add-OptionalArgument $arguments "--proxy" $ctx.Proxy
            Add-JsRuntimeArgument $arguments $ctx.JsRuntimes
            if ($ctx.UseAria2 -and $ctx.Aria2cPath) {
                $arguments.Add("--downloader")
                $arguments.Add("aria2c")
                $arguments.Add("--downloader-args")
                $arguments.Add("aria2c:--max-connection-per-server=$($ctx.Aria2Connections) --split=$($ctx.Aria2Connections) --min-split-size=1M --file-allocation=none")
            }
            Invoke-YtDlp $arguments.ToArray() $ctx
        }

        $existingSubtitle = Get-ChildItem -LiteralPath $topicDir -File -Filter "*.srt" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $existingSubtitle) {
            if ([string]::IsNullOrWhiteSpace($subtitleLanguage)) {
                $subtitleLanguage = Get-SubtitleLanguage $url $ctx
            }
            if (-not [string]::IsNullOrWhiteSpace($subtitleLanguage)) {
                $arguments = [System.Collections.Generic.List[string]]::new()
                @(
                    "--skip-download", "--no-playlist", "--write-subs", "--write-auto-subs",
                    "--sub-langs", $subtitleLanguage, "--convert-subs", "srt",
                    "-o", (Join-Path $topicDir "字幕.%(ext)s"), $url
                ) | ForEach-Object { $arguments.Add($_) }
                Add-OptionalArgument $arguments "--ffmpeg-location" $ctx.FfmpegLocation
                Add-OptionalArgument $arguments "--cookies-from-browser" $ctx.CookiesFromBrowser
                Add-OptionalArgument $arguments "--proxy" $ctx.Proxy
                Add-JsRuntimeArgument $arguments $ctx.JsRuntimes
                Invoke-YtDlp $arguments.ToArray() $ctx

                $generated = Get-ChildItem -LiteralPath $topicDir -File -Filter "字幕*.srt" -ErrorAction SilentlyContinue |
                    Sort-Object LastWriteTime -Descending |
                    Select-Object -First 1
                if ($generated -and $generated.Name -ne "字幕.srt") {
                    Move-Item -LiteralPath $generated.FullName -Destination (Join-Path $topicDir "字幕.srt") -Force
                }
            }
        }
    } catch {
        $status = $_.Exception.Message
    }

    $videoCheck = Get-ChildItem -LiteralPath $topicDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.BaseName -eq "高清源视频" -and $_.Extension -in @(".mp4", ".mkv", ".webm") } |
        Select-Object -First 1
    $subtitleCheck = Get-ChildItem -LiteralPath $topicDir -File -Filter "*.srt" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ((-not $videoCheck -or -not $subtitleCheck) -and $status -eq "complete") {
        $status = "incomplete"
    }

    $result.video = [bool]$videoCheck
    $result.subtitle = [bool]$subtitleCheck
    $result.subtitle_language = $subtitleLanguage
    $result.status = $status
    return $result
}

# ---- 主流程 ----
$manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
$rows = @(Import-Csv -LiteralPath $manifestPath -Encoding UTF8)
if ($rows.Count -eq 0) {
    throw "Manifest contains no data rows: $manifestPath"
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$logPath = Join-Path $OutputRoot $StatusCsvName

# 断点续跑：读上次状态，status=complete 且文件仍在的选题直接跳过
$previous = @{}
if (Test-Path -LiteralPath $logPath) {
    foreach ($prevRow in @(Import-Csv -LiteralPath $logPath -Encoding UTF8)) {
        $previous[[string]$prevRow.folder_name] = $prevRow
    }
}

# aria2c 探测（只探测一次）
$aria2cPath = $null
if ($UseAria2) {
    $aria2cPath = Get-Aria2cPath
    if (-not $aria2cPath) {
        Write-Warning "未找到 aria2c，跳过 aria2 多连接加速（可用: winget install aria2.aria2）"
    } else {
        Write-Output "aria2c: $aria2cPath（每文件 $Aria2Connections 连接）"
    }
}

$ctx = @{
    YtDlp = $YtDlp
    FfmpegLocation = (Get-DetectedFfmpegLocation $FfmpegLocation)
    CookiesFromBrowser = $CookiesFromBrowser
    Proxy = $Proxy
    MinHeight = $MinHeight
    MaxHeight = $MaxHeight
    OutputRoot = $OutputRoot
    UseAria2 = [bool]$UseAria2
    Aria2cPath = $aria2cPath
    Aria2Connections = $Aria2Connections
    Trace = [bool]$Trace
    JsRuntimes = (Get-DetectedJsRuntimes $JsRuntimes)
}

if ($ctx.JsRuntimes) {
    Write-Output "yt-dlp JS runtime: $($ctx.JsRuntimes)（YouTube 解析需要）"
} else {
    Write-Warning "未找到 node/deno，YouTube 解析可能失败；可安装 node 或用 -JsRuntimes 指定"
}
if (-not $ctx.FfmpegLocation) {
    Write-Warning "未找到 ffmpeg，视频/音频合并可能失败；可用 -FfmpegLocation 指定"
} else {
    Write-Output "ffmpeg: $($ctx.FfmpegLocation)"
}

if ($PlanOnly) {
    foreach ($row in $rows) {
        $topicDir = Join-Path $OutputRoot ([string]$row.folder_name)
        Write-Output "PLAN`t$([string]$row.folder_name)`t$([string]$row.url)`t$topicDir"
    }
    Write-Output "Planned $($rows.Count) topic(s). No files downloaded."
    exit 0
}

$statusRows = [System.Collections.Generic.List[object]]::new()
$skipCount = 0
$activeRows = @()

foreach ($row in $rows) {
    $folderName = [string]$row.folder_name
    $prev = $null
    if ($folderName -and $previous.ContainsKey($folderName)) { $prev = $previous[$folderName] }
    # manifest 显式 ready 标记 或 上次 complete 且目录文件齐全 → 跳过
    $manifestReady = [string]$row.status -eq "ready"
    if ($manifestReady -or ($prev -and [string]$prev.status -eq "complete")) {
        $topicDir = Join-Path $OutputRoot $folderName
        $videoOk = Get-ChildItem -LiteralPath $topicDir -File -ErrorAction SilentlyContinue |
            Where-Object { $_.BaseName -eq "高清源视频" -and $_.Extension -in @(".mp4", ".mkv", ".webm") } |
            Select-Object -First 1
        $subtitleOk = Get-ChildItem -LiteralPath $topicDir -File -Filter "*.srt" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($videoOk -and $subtitleOk) {
            $statusRows.Add([pscustomobject]@{
                folder_name = $folderName
                url = [string]$row.url
                video = $true
                subtitle = $true
                subtitle_language = [string]$row.subtitle_language
                attempts = 0
                status = "ready(skip)"
            })
            $skipCount++
            continue
        }
    }
    $activeRows += , $row
}

$canParallel = ($Parallel -gt 1 -and $PSVersionTable.PSVersion.Major -ge 7)
if ($Parallel -gt 1 -and -not $canParallel) {
    Write-Warning "并行需要 PowerShell 7+（当前 $($PSVersionTable.PSVersion)），降级为顺序执行"
}

if ($activeRows.Count -gt 0) {
    Write-Output "待处理 $($activeRows.Count) 个选题（已跳过 $skipCount 个完成项）…"
    if ($canParallel) {
        $parallelResults = $activeRows | ForEach-Object -Parallel {
            $rowScript = $using:processRowScript
            $rowCtx = $using:ctx
            @($rowScript.Invoke($_, $rowCtx)) |
                Where-Object { $_ -is [System.Management.Automation.PSCustomObject] } |
                Select-Object -Last 1
        } -ThrottleLimit $Parallel
        foreach ($item in $parallelResults) { $statusRows.Add($item) }
    } else {
        foreach ($row in $activeRows) {
            $result = @($processRowScript.Invoke($row, $ctx)) |
                Where-Object { $_ -is [System.Management.Automation.PSCustomObject] } |
                Select-Object -Last 1
            $statusRows.Add($result)
        }
    }
}

$statusRows | Export-Csv -LiteralPath $logPath -Encoding UTF8 -NoTypeInformation
$complete = @($statusRows | Where-Object { $_.video -and $_.subtitle }).Count
$failed = @($statusRows | Where-Object { $_.status -notin @("complete", "ready(skip)") -and $_.status -notlike "ready*" }).Count
Write-Output "Completed $complete/$($rows.Count) topic(s) (skipped $skipCount). Status: $logPath"
if ($failed -gt 0) {
    Write-Output "Failed/incomplete: $failed topic(s) — 见 $logPath 的 status 列"
}
