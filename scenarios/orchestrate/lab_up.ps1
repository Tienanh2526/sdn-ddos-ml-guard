<#
lab_up.ps1 — Bật lab và đưa về trạng thái SẴN SÀNG THU DỮ LIỆU (chạy mỗi sáng / sau khi tắt VM).

Tự khởi động sau boot (không cần làm gì): ODL (opendaylight.service), flow proactive trên
switch (restore-sdn-flows.service), VXLAN host (restore-host-vxlan.service), web :8080 (sdn-web).
MẤT khi tắt VM -> script này dựng lại:
  1. rule reactive Pha B trên vm-leaf2, vm-leaf1 (scenarios/vm-leaf/reactive_mode.sh apply)
  2. dịch vụ UDP echo trên vm-web (aiguard-udp-echo)
  3. (một lần) cài hping3 trên vm-attacker + vm-client cho A06
Rồi kiểm tra client -> web HTTP 200. In "LAB_READY" nếu mọi thứ OK.

  .\scenarios\orchestrate\lab_up.ps1            # bật VM + chuẩn bị
  .\scenarios\orchestrate\lab_up.ps1 -NoStart   # VM đang chạy, chỉ chuẩn bị/kiểm tra
#>
param([switch]$NoStart, [string]$Zone = "asia-southeast1-a", [string]$User = "anh2004zai_gmail_com")
$ErrorActionPreference = "Continue"
$gcloud = "C:\Users\NGOC TU\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$VMS = "vm-controller", "vm-spine1", "vm-spine2", "vm-leaf1", "vm-leaf2", "vm-web", "vm-client", "vm-attacker"
$H = "/home/$User"

# Write-Host (không trả về pipeline) để log trong WaitFor không lẫn vào giá trị true/false trả về.
function Log($m) { Write-Host ("{0:HH:mm:ss} {1}" -f (Get-Date), $m) }
function Remote([string]$vm, [string]$script) {
  $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($script -replace "`r`n", "`n")))
  for ($i = 1; $i -le 5; $i++) {
    $out = & $gcloud compute ssh $vm --zone $Zone --tunnel-through-iap --quiet --command "echo $b64 | base64 -d | bash" 2>&1 | ForEach-Object { "$_" }
    if (($out -join "`n") -notmatch 'FATAL ERROR|Connection closed by remote|failed to connect to backend|exited with return code \[255\]') { break }
    Start-Sleep 10
  }
  $out | Where-Object { $_ -notmatch 'WARNING|Invalid characters|At line|~~~|CategoryInfo|FullyQualifiedErrorId|NativeCommandError|^\s*$|private SSH key|troubleshoot|ecommendation|IAP tunneling|External IP' }
}
function WaitFor([string]$vm, [string]$check, [string]$okPattern, [int]$maxMin, [string]$what) {
  $deadline = (Get-Date).AddMinutes($maxMin)
  while ((Get-Date) -lt $deadline) {
    $r = (Remote $vm $check) -join " "
    if ($r -match $okPattern) { Log "OK  $what ($r)"; return $true }
    Log "... chờ $what ($r)"; Start-Sleep 20
  }
  Log "FAIL $what sau $maxMin phút"; return $false
}

if (-not $NoStart) {
  Log "Bật 8 VM..."
  & $gcloud compute instances start @VMS --zone $Zone 2>&1 | Out-Null
  Start-Sleep 30
}
$ok = $true
$ok = (WaitFor "vm-controller" "sudo ss -tn state established '( sport = :6653 )' | grep -c 6653" '^\s*4\s*$' 12 "ODL đủ 4 kết nối OpenFlow") -and $ok
foreach ($leaf in "vm-leaf2", "vm-leaf1") {
  $ok = (WaitFor $leaf "sudo ovs-ofctl -O OpenFlow13 dump-flows br-sdn | grep -c 'priority=450,in_port'" '^\s*[1-9]' 8 "$leaf flow proactive đã khôi phục") -and $ok
  $r = Remote $leaf "sudo bash $H/scenarios/vm-leaf/reactive_mode.sh apply --idle 10 | grep -E 'APPLY_DONE|HOST_PORT|rror'"
  $r | ForEach-Object { Log "  $_" }
  if (-not (($r -join " ") -match "APPLY_DONE")) { $ok = $false }
}
$r = Remote "vm-web" "systemctl is-active --quiet aiguard-udp-echo || sudo systemd-run --unit=aiguard-udp-echo --collect python3 $H/scenarios/vm-web/udp_echo.py >/dev/null; sleep 2; echo echo=`$(systemctl is-active aiguard-udp-echo) web=`$(systemctl is-active sdn-web)"
Log "vm-web: $r"; if (-not (($r -join " ") -match "echo=active web=active")) { $ok = $false }
foreach ($vm in "vm-attacker", "vm-client") {
  $r = Remote $vm "command -v hping3 >/dev/null || (sudo DEBIAN_FRONTEND=noninteractive apt-get -qq update >/dev/null && sudo DEBIAN_FRONTEND=noninteractive apt-get -y -qq install hping3 >/dev/null 2>&1); echo hping3=`$(command -v hping3 >/dev/null && echo ok || echo MISSING)"
  Log "${vm}: $r"
}
$r = Remote "vm-client" "c=0; for i in 1 2 3 4 5; do [ `$(curl -s -o /dev/null -m 3 -w '%{http_code}' http://192.168.200.12:8080/) = 200 ] && c=`$((c+1)); done; echo HTTP_OK=`$c/5"
Log "client->web: $r"; if (-not (($r -join " ") -match "HTTP_OK=5/5")) { $ok = $false }

if ($ok) { Log "LAB_READY — có thể chạy run_scenario.ps1" } else { Log "LAB_NOT_READY — xem các dòng FAIL ở trên" }
