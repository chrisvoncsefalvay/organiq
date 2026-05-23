param(
    [string]$ImportCache = (Join-Path $env:LOCALAPPDATA "ov\data\kit\isaac-sim full\5.1\exts\3"),
    [string]$IsaacUserConfig = (Join-Path $env:LOCALAPPDATA "ov\data\Kit\Isaac-Sim Full\5.1\user.config.json")
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ExtensionName = "com.chrisvoncsefalvay.organiq"
$ExtensionVersion = "0.1.0"
$Archive = Join-Path $RepoRoot "dist\$ExtensionName-$ExtensionVersion.zip"
$DevFolder = (Resolve-Path (Join-Path $RepoRoot "source\extensions")).Path.Replace("\", "/").ToLowerInvariant()
$Target = Join-Path $ImportCache "$ExtensionName-$ExtensionVersion"

function Assert-ChildPath {
    param(
        [string]$Parent,
        [string]$Child
    )
    $ResolvedParent = [System.IO.Path]::GetFullPath($Parent)
    $ResolvedChild = [System.IO.Path]::GetFullPath($Child)
    if (-not $ResolvedChild.StartsWith($ResolvedParent, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify path outside import cache: $ResolvedChild"
    }
}

if (-not (Test-Path -LiteralPath $Archive)) {
    throw "Release archive not found: $Archive"
}

New-Item -ItemType Directory -Force -Path $ImportCache | Out-Null
Assert-ChildPath -Parent $ImportCache -Child $Target

$StaleNames = @(
    "chrisvoncsefalvay-organiq-linux-x86_64-v$ExtensionVersion",
    "chrisvoncsefalvay-organiq-windows-x86_64-v$ExtensionVersion",
    "$ExtensionName-$ExtensionVersion"
)

foreach ($Name in $StaleNames) {
    $Path = Join-Path $ImportCache $Name
    Assert-ChildPath -Parent $ImportCache -Child $Path
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

New-Item -ItemType Directory -Force -Path $Target | Out-Null
Expand-Archive -LiteralPath $Archive -DestinationPath $Target -Force

if (Test-Path -LiteralPath $IsaacUserConfig) {
    $BackupPath = "$IsaacUserConfig.organiq-backup"
    Copy-Item -LiteralPath $IsaacUserConfig -Destination $BackupPath -Force
    $Config = Get-Content -LiteralPath $IsaacUserConfig -Raw | ConvertFrom-Json
    if (-not $Config.persistent) {
        $Config | Add-Member -NotePropertyName persistent -NotePropertyValue ([pscustomobject]@{})
    }
    if (-not $Config.persistent.app) {
        $Config.persistent | Add-Member -NotePropertyName app -NotePropertyValue ([pscustomobject]@{})
    }
    if (-not $Config.persistent.app.exts) {
        $Config.persistent.app | Add-Member -NotePropertyName exts -NotePropertyValue ([pscustomobject]@{})
    }
    if (-not $Config.persistent.app.exts.userFolders) {
        $Config.persistent.app.exts | Add-Member -NotePropertyName userFolders -NotePropertyValue ([pscustomobject]@{})
    }
    $Config.persistent.app.exts.userFolders."0" = $DevFolder
    $Config | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $IsaacUserConfig -Encoding UTF8
    Write-Output "user_config=$IsaacUserConfig"
    Write-Output "user_config_backup=$BackupPath"
    Write-Output "user_folder=$DevFolder"
}

Write-Output "import_root=$Target"
Write-Output "archive=$Archive"
