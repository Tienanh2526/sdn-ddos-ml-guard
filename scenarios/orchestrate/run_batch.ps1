<#
run_batch.ps1 — Chạy NỐI TIẾP nhiều lần thu (mỗi dòng 1 run). Lỗi ở 1 lần không dừng cả lô.

  .\scenarios\orchestrate\run_batch.ps1 -Runs A05-R1,A06-R1,A07-R1

Bảng định nghĩa dưới đây khớp kế hoạch tráo vai RUNBOOK 0.4: R1–R3 máy mặc định, R4–R5 tráo vai.
#>
param([Parameter(Mandatory = $true)][string[]]$Runs)
$here = $PSScriptRoot
$PLAN = @{
  # kịch bản: (máy mặc định, lệnh, thời lượng s, máy tráo vai, --src tráo vai)
  "N04" = @("vm-client",   "python3 ~/scenarios/vm-client/flash_crowd_n04.py",   600, "vm-attacker", "192.168.200.13")
  "A04" = @("vm-attacker", "python3 ~/scenarios/vm-attacker/lowrate_a04.py",      780, "vm-client",   "192.168.200.11")
  "A05" = @("vm-attacker", "python3 ~/scenarios/vm-attacker/pulsing_a05.py",      520, "vm-client",   "192.168.200.11")
  "A06" = @("vm-attacker", "python3 ~/scenarios/vm-attacker/synflood_a06.py --yes", 780, "vm-client", "192.168.200.11")
  "A07" = @("vm-attacker", "python3 ~/scenarios/vm-attacker/flowburst_a07.py",    780, "vm-client",   "192.168.200.11")
}
foreach ($run in $Runs) {
  $scn, $rep = $run.Split("-"); $mit = $rep.StartsWith("M"); $n = [int]$rep.Substring(1)
  $p = $PLAN[$scn]; if (-not $p) { Write-Host "Bỏ qua $run (kịch bản lạ)"; continue }
  $vm, $cmd = $p[0], $p[1]
  if (-not $mit -and $n -ge 4) { $vm = $p[3]; $cmd = "$($p[1]) --src $($p[4])" }
  $extra = if ($mit) { @{ Detector = "mitigate"; AllowDrop = $true } } else { @{} }
  Write-Host ("{0:HH:mm:ss} >>> BẮT ĐẦU {1} trên {2}{3}" -f (Get-Date), $run, $vm, $(if ($mit) { " + module giảm thiểu" } else { "" }))
  try { & (Join-Path $here "run_scenario.ps1") -RunId $run -GenVm $vm -GenCmd $cmd -GenDuration $p[2] @extra }
  catch { Write-Host "!!! $run lỗi: $_" }
  Write-Host ("{0:HH:mm:ss} <<< XONG {1}" -f (Get-Date), $run)
}
