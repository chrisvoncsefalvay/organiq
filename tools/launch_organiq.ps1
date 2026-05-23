param(
    [string]$IsaacRoot = $env:ISAACSIM_ROOT,
    [string]$Kit = "",
    [string]$Experience = "",
    [switch]$Base
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ExtFolder = Join-Path $RepoRoot "source\extensions"
$ShowScript = Join-Path $PSScriptRoot "show_organiq_window.py"

function Find-IsaacRoot {
    param([string]$RequestedRoot)

    $Candidates = @()
    if ($RequestedRoot) {
        $Candidates += $RequestedRoot
    }
    if ($env:ISAAC_SIM_ROOT) {
        $Candidates += $env:ISAAC_SIM_ROOT
    }
    if ($env:OMNI_USER_PACKAGE_ROOT -and (Test-Path -LiteralPath $env:OMNI_USER_PACKAGE_ROOT)) {
        $Candidates += Get-ChildItem -LiteralPath $env:OMNI_USER_PACKAGE_ROOT -Directory -Filter "isaac-sim*" |
            Sort-Object Name -Descending |
            ForEach-Object { $_.FullName }
    }
    if ($env:LOCALAPPDATA) {
        $PackageRoot = Join-Path $env:LOCALAPPDATA "ov\pkg"
        if (Test-Path -LiteralPath $PackageRoot) {
            $Candidates += Get-ChildItem -LiteralPath $PackageRoot -Directory -Filter "isaac-sim*" |
                Sort-Object Name -Descending |
                ForEach-Object { $_.FullName }
        }
    }

    foreach ($Candidate in $Candidates) {
        if (-not $Candidate) {
            continue
        }
        $KitPath = Join-Path $Candidate "kit\kit.exe"
        if (Test-Path -LiteralPath $KitPath) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }
    return ""
}

if (-not $Kit -or -not $Experience) {
    $ResolvedIsaacRoot = Find-IsaacRoot -RequestedRoot $IsaacRoot
    if (-not $ResolvedIsaacRoot) {
        throw "Isaac Sim root was not found. Set ISAACSIM_ROOT or pass -Kit and -Experience."
    }
    if (-not $Kit) {
        $Kit = Join-Path $ResolvedIsaacRoot "kit\kit.exe"
    }
    if (-not $Experience) {
        $ExperienceName = "isaacsim.exp.full.kit"
        if ($Base) {
            $ExperienceName = "isaacsim.exp.base.kit"
        }
        $Experience = Join-Path $ResolvedIsaacRoot (Join-Path "apps" $ExperienceName)
    }
} elseif ($Base) {
    throw "-Base is only used when -Experience is not specified."
}

if (-not (Test-Path -LiteralPath $Kit)) {
    throw "Kit executable not found: $Kit"
}

if (-not (Test-Path -LiteralPath $Experience)) {
    throw "Isaac experience not found: $Experience"
}

$RequiredExtensions = @(
    "omni.timeline",
    "omni.anim.window.timeline",
    "omni.anim.widget.timeline",
    "omni.kit.widget.timeline",
    "omni.physx",
    "omni.physx.bundle",
    "omni.physx.ui",
    "omni.physx.commands",
    "omni.usdphysics.ui",
    "omni.physics.physx",
    "omni.physics.stageupdate",
    "com.chrisvoncsefalvay.organiq"
)

$Args = @($Experience, "--ext-folder", $ExtFolder)
foreach ($Extension in $RequiredExtensions) {
    $Args += @("--enable", $Extension)
}
$Args += @("--exec", $ShowScript)

& $Kit @Args

