<#
  WindLab pre-install check for Windows 11 (run BEFORE installing WSL / WindLab).

  UNTESTED ON WINDOWS (written 2026-11-03 for the v0.1 student trial). Read-only: it
  changes nothing and needs no administrator rights.

  How to run it (in a normal PowerShell window, from the folder you saved it in):
      powershell -ExecutionPolicy Bypass -File .\preflight.ps1

  It checks: Windows version, memory, processor cores, virtualization in the firmware
  (BIOS/UEFI), whether WSL and Ubuntu 24.04 are installed, and free disk space on C:.
  Every line says PASS, WARN or FAIL, and what to do if something is wrong.
#>

$ErrorActionPreference = 'Continue'
$script:fails = 0
$script:warns = 0

function Report([string]$status, [string]$name, [string]$message, [string]$fix = '') {
    $tag = '[' + $status + ']'
    $color = 'Green'
    if ($status -eq 'WARN') { $color = 'Yellow'; $script:warns++ }
    if ($status -eq 'FAIL') { $color = 'Red'; $script:fails++ }
    if ($status -eq 'INFO') { $color = 'Gray' }
    Write-Host ("{0} {1}: {2}" -f $tag, $name, $message) -ForegroundColor $color
    if ($fix -ne '' -and $status -ne 'PASS') { Write-Host ("       -> " + $fix) }
}

Write-Host ''
Write-Host 'WindLab pre-install check (read-only, no changes are made)'
Write-Host ''

# ---------------------------------------------------------------- Windows version
try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    $build = [int]$os.BuildNumber
    $name = "$($os.Caption) (build $build, $($os.OSArchitecture))"
    if ($build -ge 22000) {
        Report 'PASS' 'Windows' $name
    } elseif ($build -ge 19041) {
        Report 'WARN' 'Windows' "$name - this is Windows 10." 'WindLab is planned for Windows 11; WSL2 also works on Windows 10 version 2004 or later, but this guide has not been tried there.'
    } else {
        Report 'FAIL' 'Windows' "$name is too old for WSL2." 'Update Windows (Settings > Windows Update).'
    }
    if ($os.OSArchitecture -notmatch '64') {
        Report 'FAIL' 'Windows' 'a 64-bit Windows is needed.' 'WindLab cannot run on 32-bit Windows.'
    }
} catch {
    Report 'WARN' 'Windows' 'could not read the Windows version.' 'Check it in Settings > System > About.'
}

# ---------------------------------------------------------------- memory
try {
    $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
    $gb = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
    if ($gb -ge 15) {
        Report 'PASS' 'Memory' "$gb GB."
    } elseif ($gb -ge 8) {
        Report 'WARN' 'Memory' "$gb GB - WindLab is planned for 16 GB or more." 'Quick runs need about 1-2 GB inside Linux; close SolidWorks and other large programs during a run.'
    } else {
        Report 'FAIL' 'Memory' "$gb GB is not enough." 'WindLab needs at least 8 GB (16 GB recommended).'
    }
} catch {
    Report 'WARN' 'Memory' 'could not read the memory size.' 'Check it in Settings > System > About.'
    $cs = $null
}

# ---------------------------------------------------------------- processor
try {
    $cpus = @(Get-CimInstance Win32_Processor -ErrorAction Stop)
    $cores = ($cpus | Measure-Object -Property NumberOfCores -Sum).Sum
    $logical = ($cpus | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
    $model = ($cpus[0].Name -replace '\s+', ' ').Trim()
    if ($cores -ge 4) {
        Report 'PASS' 'Processor' "$model, $cores cores ($logical logical)."
    } elseif ($cores -ge 2) {
        Report 'WARN' 'Processor' "$model, $cores cores ($logical logical) - runs will be slow." 'It will work, but a Quick run may take 10 minutes or more.'
    } else {
        Report 'FAIL' 'Processor' "$model, $cores core." 'WindLab needs at least 2 cores.'
    }
} catch {
    Report 'WARN' 'Processor' 'could not read the processor details.' ''
    $cpus = @()
}

# ---------------------------------------------------------------- virtualization
# When Hyper-V / WSL2 is already running, Windows reports the hypervisor as present and the
# firmware flag reads False (the hypervisor owns the feature) - both mean "fine".
$hyper = $false
if ($cs -ne $null) { $hyper = [bool]$cs.HypervisorPresent }
$fw = $null
if ($cpus.Count -gt 0) { $fw = $cpus[0].VirtualizationFirmwareEnabled }
if ($hyper) {
    Report 'PASS' 'Virtualization' 'a hypervisor is already running (WSL2 or Hyper-V), so virtualization is on.'
} elseif ($fw -eq $true) {
    Report 'PASS' 'Virtualization' 'enabled in the firmware (BIOS/UEFI).'
} elseif ($fw -eq $false) {
    Report 'FAIL' 'Virtualization' 'turned off in the firmware (BIOS/UEFI).' 'Restart into the BIOS/UEFI setup (often F2, F10 or Del while starting; on Lenovo also the Novo button) and enable "Intel Virtualization Technology" / "VT-x" or "SVM Mode" (AMD). Ask your instructor if unsure.'
} else {
    Report 'WARN' 'Virtualization' 'could not be checked.' 'Open Task Manager > Performance > CPU and look for "Virtualization: Enabled".'
}

# ---------------------------------------------------------------- WSL
$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if ($wsl -eq $null) {
    Report 'INFO' 'WSL' 'not installed yet - Step 1 of the install guide installs it.'
} else {
    # wsl.exe writes UTF-16 text; read it as such and drop stray NUL characters
    $oldEnc = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = [System.Text.Encoding]::Unicode
        $status = (& wsl.exe --status 2>&1 | Out-String) -replace "`0", ''
        $list = (& wsl.exe --list --verbose 2>&1 | Out-String) -replace "`0", ''
        $code = $LASTEXITCODE
    } catch {
        $status = ''; $list = ''; $code = 1
    } finally {
        [Console]::OutputEncoding = $oldEnc
    }
    if ($list -match 'Ubuntu-24\.04') {
        if ($list -match 'Ubuntu-24\.04\s+\S+\s+2') {
            Report 'PASS' 'WSL' 'Ubuntu 24.04 is installed and uses WSL 2.'
        } else {
            Report 'WARN' 'WSL' 'Ubuntu 24.04 is installed but may be using WSL 1.' 'In PowerShell run:  wsl --set-version Ubuntu-24.04 2'
        }
    } elseif ($code -eq 0 -and $list -match '\S') {
        Report 'INFO' 'WSL' 'WSL is installed, but not Ubuntu 24.04 yet - Step 1 of the install guide adds it.'
    } else {
        Report 'INFO' 'WSL' 'WSL is not set up yet - Step 1 of the install guide does it (needs an administrator account).'
    }
    if ($status -match 'Default Version:\s*1') {
        Report 'WARN' 'WSL' 'the default WSL version is 1.' 'In PowerShell run:  wsl --set-default-version 2'
    }
}

# ---------------------------------------------------------------- disk
try {
    $c = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'" -ErrorAction Stop
    $free = [math]::Round($c.FreeSpace / 1GB, 0)
    if ($free -ge 20) {
        Report 'PASS' 'Disk space' "$free GB free on C:."
    } elseif ($free -ge 10) {
        Report 'WARN' 'Disk space' "$free GB free on C:." 'Ubuntu and OpenFOAM need about 5 GB and WindLab keeps up to 10 GB of runs; 20 GB free is comfortable.'
    } else {
        Report 'FAIL' 'Disk space' "only $free GB free on C:." 'Free at least 10 GB (Settings > System > Storage).'
    }
} catch {
    Report 'WARN' 'Disk space' 'could not read the free space on C:.' ''
}

# ---------------------------------------------------------------- administrator (information only)
try {
    $me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    $groups = [Security.Principal.WindowsIdentity]::GetCurrent().Groups | ForEach-Object { $_.Value }
    if ($groups -contains 'S-1-5-32-544' -or $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Report 'INFO' 'Account' 'your account is an administrator: you can do Step 1 (Terminal (Admin)).'
    } else {
        Report 'WARN' 'Account' 'your account does not seem to be an administrator.' 'Installing WSL (Step 1) needs an administrator account; ask whoever manages the laptop.'
    }
} catch { }

Write-Host ''
if ($script:fails -gt 0) {
    Write-Host "$($script:fails) problem(s) must be fixed before installing WindLab (see the -> lines)." -ForegroundColor Red
} elseif ($script:warns -gt 0) {
    Write-Host "You can install WindLab. $($script:warns) warning(s) above are worth a look." -ForegroundColor Yellow
} else {
    Write-Host 'All good: continue with Step 1 of the install guide.' -ForegroundColor Green
}
Write-Host ''
