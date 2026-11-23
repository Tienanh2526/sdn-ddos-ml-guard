<#
run_scenario.ps1 — Chạy trọn 1 lần thu dữ liệu (1 run_id) từ máy Windows có gcloud.

Làm tự động đúng trình tự RUNBOOK mục 1–3:
  T0-40s  vm-controller: tcpdump :6653 + telemetry ; vm-leaf1: dataplane_collector ;
          vm-client: HTTP QoS monitor
  T0-30s  vm-client: lưu lượng nền background_mix.py (luôn chạy trên vm-client)
  T0      máy sinh lưu lượng: generator (--run <RunId> [--src ...])
  hết giờ gom groundtruth/dataplane/http về controller -> finalize_run.py gán nhãn
          -> tải về data/raw/vm-controller/sdn-ai/dataset/<attack|normal>/<SCN>/<RunId>/

Ví dụ:
  .\scenarios\orchestrate\run_scenario.ps1 -RunId A04-R1 -GenVm vm-attacker -GenCmd "python3 ~/scenarios/vm-attacker/lowrate_a04.py" -GenDuration 780
  .\scenarios\orchestrate\run_scenario.ps1 -RunId A04-R4 -GenVm vm-client -GenCmd "python3 ~/scenarios/vm-attacker/lowrate_a04.py --src 192.168.200.11" -GenDuration 780
Điều kiện: reactive_mode đã apply (2 leaf), aiguard-udp-echo active (vm-web), scenarios/ đã chép lên VM.
ODL dùng tài khoản admin; mật khẩu lấy từ biến môi trường ODL_PASSWORD (mặc định admin).
#>
param(
  [Parameter(Mandatory = $true)][string]$RunId,
  [Parameter(Mandatory = $true)][string]$GenVm,
  [Parameter(Mandatory = $true)][string]$GenCmd,
  [Parameter(Mandatory = $true)][int]$GenDuration,
  [string]$Zone = "asia-southeast1-a",
  [string]$User = "anh2004zai_gmail_com",
  [int]$Lead = 40,      # giây thu trước generator
  [int]$Tail = 90,      # giây thu sau generator
  # Chạy kèm module thời gian thực trên vm-controller (cần deploy_detector.ps1 trước):
  #   none = không ; detect = chỉ phát hiện ; mitigate = phát hiện + cấp 1 ; thêm -AllowDrop cho cấp 2
  [ValidateSet("none", "detect", "mitigate")][string]$Detector = "none",
  [switch]$AllowDrop,
  # máy đo HTTP người dùng hợp lệ (tráo vai + giảm thiểu: vm-client là máy tấn công -> đo từ vm-attacker)
  [string]$ProbeVm = "vm-client"
)
$ErrorActionPreference = "Continue"   # PS 5.1: stderr của gcloud bị coi là lỗi nếu để Stop
$gcloud = "C:\Users\NGOC TU\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$scn = $RunId.Split("-")[0]
$cat = if ($scn.StartsWith("N")) { "normal" } else { "attack" }
$H = "/home/$User"
$R = "$H/aiguard-runs/$RunId"
$cap = $Lead + $GenDuration + $Tail
$samples = [math]::Ceiling($cap / 2)
$odlPw = if ($env:ODL_PASSWORD) { $env:ODL_PASSWORD } else { "admin" }
# Thư mục tạm NẰM TRONG repo (gitignore): $env:TEMP có dạng tên ngắn "NGOCTU~1" mà Push-Location
# hiểu sai dấu "~" -> file rơi ra thư mục hiện tại.
$work = Join-Path $repo ".runs-tmp\$RunId"
if (Test-Path -LiteralPath $work) { Rename-Item -LiteralPath $work "$RunId.bak-$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())" }
New-Item -ItemType Directory -Force $work | Out-Null
$work = (Get-Item -LiteralPath $work).FullName
$GenCmd = $GenCmd -replace "~/", "$H/"     # systemd-run --uid không chắc có HOME -> đường dẫn tuyệt đối
# Có giảm thiểu cấp 2: máy tấn công bị chặn tới web là mong đợi -> generator không được coi đó là lab hỏng
if ($Detector -eq "mitigate" -and $AllowDrop) { $GenCmd = "AIGUARD_EXPECT_BLOCK=1 $GenCmd" }

function Log($m) { "{0:HH:mm:ss} {1}" -f (Get-Date), $m | Tee-Object -FilePath (Join-Path $work "orchestrate.log") -Append }

function Remote([string]$vm, [string]$script) {
  $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($script -replace "`r`n", "`n")))
  for ($i = 1; $i -le 4; $i++) {
    $out = & $gcloud compute ssh $vm --zone $Zone --tunnel-through-iap --quiet --command "echo $b64 | base64 -d | bash" 2>&1 | ForEach-Object { "$_" }
    $txt = $out -join "`n"
    if ($txt -notmatch 'FATAL ERROR|Connection closed by remote|failed to connect to backend|exited with return code \[255\]') { break }
    Start-Sleep 5
  }
  $out | Where-Object { $_ -notmatch 'WARNING|Invalid characters|At line|~~~|CategoryInfo|FullyQualifiedErrorId|NativeCommandError|^\s*$|private SSH key|troubleshoot|ecommendation|IAP tunneling|External IP' }
}
# gcloud scp trên Windows hiểu "C:\..." là tên máy "C" -> luôn dùng đường dẫn TƯƠNG ĐỐI.
function Pull([string]$vm, [string]$src, [string]$dstDir) {
  Push-Location -LiteralPath $dstDir
  try {
    for ($i = 1; $i -le 4; $i++) {
      & $gcloud compute scp "${vm}:$src" "." --zone $Zone --tunnel-through-iap --quiet 2>&1 | Out-Null
      if ($LASTEXITCODE -eq 0) { return $true }; Start-Sleep 5
    }
    Log "PULL FAILED ${vm}:$src"; return $false
  } finally { Pop-Location }
}
function Push([string]$vm, [string]$localDir, [string]$name, [string]$dst) {
  Push-Location -LiteralPath $localDir
  try {
    for ($i = 1; $i -le 4; $i++) {
      & $gcloud compute scp $name "${vm}:$dst" --zone $Zone --tunnel-through-iap --quiet 2>&1 | Out-Null
      if ($LASTEXITCODE -eq 0) { return $true }; Start-Sleep 5
    }
    Log "PUSH FAILED $name -> ${vm}:$dst"; return $false
  } finally { Pop-Location }
}

Log "=== $RunId  gen=$GenVm  dur=${GenDuration}s  capture=${cap}s samples=$samples"
# Chạy lại cùng RunId: các bộ thu (đúng) từ chối ghi đè file cũ -> lần chạy mới mất telemetry/HTTP và tải về
# nhầm file cũ (A06-M1 lần 2). -> Cất thư mục cũ trên MỌI VM tham gia thành $R.bak-<epoch> trước khi bắt đầu.
foreach ($vm in @("vm-controller", "vm-leaf1", "vm-client", $GenVm, $ProbeVm) | Select-Object -Unique) {
  Remote $vm "if [ -d $R ]; then mv $R $R.bak-`$(date +%s) && echo MOVED_OLD; fi" | ForEach-Object { if ($_ -match 'MOVED_OLD') { Log "  $vm : cất thư mục $RunId cũ sang .bak" } }
}
$now = [int64]((Remote "vm-controller" "date +%s") | Select-String '^\d+$' | Select-Object -First 1).ToString()
$T0 = $now + 150
Log "T0=$T0 (UTC $([DateTimeOffset]::FromUnixTimeSeconds($T0).UtcDateTime.ToString('HH:mm:ss')))"
$at = { param($off) "sleep `$(( $T0 + $off - `$(date +%s) ))" }

# --- module thời gian thực (tùy chọn): leaf1 đẩy đặc trưng data-plane sang controller:47000
$pushArg = ""; $detLine = ""
if ($Detector -ne "none") {
  $pushArg = "--push 10.10.0.10:47000"
  # Bản collector trên leaf1 phải có --push (A06-M1 lần đầu hỏng vì leaf1 còn bản cũ -> 0 đặc trưng dp_*,
  # detector p=nan suốt lần chạy) -> luôn đồng bộ bản trong repo trước, lỗi thì dừng.
  if (-not (Push "vm-leaf1" (Join-Path $repo "scenarios\vm-leaf") "dataplane_collector.py" "$H/scenarios/vm-leaf/")) {
    Log "Không chép được dataplane_collector.py lên vm-leaf1 -> dừng"; exit 1 }
  $dropArg = if ($AllowDrop) { "--allow-drop" } else { "" }
  # chạy bằng root (tshark cần quyền bắt gói); bật cùng lúc với telemetry, dừng khi hết thời gian thu
  $detLine = "sudo systemd-run --unit=aig-$RunId-det --collect bash -c '$(& $at (-$Lead)); cd $H/aiguard-app/realtime && $H/aiguard-venv/bin/python detector.py --model $H/aiguard-app/models/detector_rf.joblib --telemetry-module $H/aiguard-tools/telemetry_collector_a03.py --mode $Detector $dropArg --password $odlPw --duration $cap --out-dir $R/detector > $R/detector.log 2>&1'"
  Log "Module thời gian thực: $Detector $dropArg"
}

# --- controller: pcap + telemetry
Remote "vm-controller" @"
mkdir -p $R; sudo mkdir -p /var/tmp/aiguard-runs/$RunId
sudo systemd-run --unit=aig-$RunId-pcap --collect bash -c '$(& $at (-$Lead)); timeout --signal=INT $cap tcpdump -Z root -i ens4 -nn -s0 -w /var/tmp/aiguard-runs/$RunId/openflow.pcap "tcp port 6653"'
sudo systemd-run --unit=aig-$RunId-tel --collect --uid=$User bash -c '$(& $at (-$Lead)); SDN_SAMPLES=$samples SDN_INTERVAL=2 A03_TELEMETRY_OUTPUT=$R/telemetry.csv SDN_AUTH_STDIN=1 python3 $H/aiguard-tools/telemetry_collector_a03.py <<< "$odlPw" > $R/telemetry.log 2>&1'
$detLine
echo scheduled-controller
"@ | ForEach-Object { Log $_ }
# --- leaf1: data-plane
Remote "vm-leaf1" @"
mkdir -p $R
sudo systemd-run --unit=aig-$RunId-dp --collect bash -c '$(& $at (-$Lead)); python3 $H/scenarios/vm-leaf/dataplane_collector.py --out $R/dataplane.csv --samples $samples $pushArg > $R/dataplane.log 2>&1'
echo scheduled-leaf1
"@ | ForEach-Object { Log $_ }
# --- QoS người dùng (mặc định vm-client) + lưu lượng nền (luôn vm-client)
Remote $ProbeVm @"
mkdir -p $R
sudo systemd-run --unit=aig-$RunId-qos --collect --uid=$User bash -c '$(& $at (-$Lead)); A03_HTTP_OUTPUT=$R/http.csv A03_HTTP_SAMPLES=$samples python3 $H/aiguard-tools/http_qos_monitor_a03.py > $R/http.log 2>&1'
echo scheduled-probe-$ProbeVm
"@ | ForEach-Object { Log $_ }
Remote "vm-client" @"
mkdir -p $R
sudo systemd-run --unit=aig-$RunId-bg --collect --uid=$User bash -c '$(& $at (-30)); python3 $H/scenarios/vm-client/background_mix.py --duration $($cap - 20) > $R/background.log 2>&1'
echo scheduled-client
"@ | ForEach-Object { Log $_ }
# --- generator
$sudoGen = if ($GenCmd -match "synflood") { "" } else { "--uid=$User" }
Remote $GenVm @"
mkdir -p $R
sudo systemd-run --unit=aig-$RunId-gen --collect $sudoGen bash -c '$(& $at 0); cd $H && $GenCmd --run $RunId --out-dir $R > $R/generator.log 2>&1'
echo scheduled-generator
"@ | ForEach-Object { Log $_ }

$left = $T0 - [int64]((Remote "vm-controller" "date +%s") | Select-String '^\d+$' | Select-Object -First 1).ToString()
Log "Tất cả đã hẹn giờ. Còn ${left}s tới T0; kết thúc thu sau ~$($left + $GenDuration + $Tail + 15)s"
Start-Sleep ($left + $GenDuration + $Tail + 15)

# --- kiểm tra generator xong
Log ((Remote $GenVm "tail -4 $R/generator.log") -join " | ")

# --- gom về controller
foreach ($p in @(@($GenVm, "$R/$RunId.groundtruth.json"), @("vm-leaf1", "$R/dataplane.csv"), @("vm-leaf1", "$R/dataplane.log"),
                 @($ProbeVm, "$R/http.csv"), @($ProbeVm, "$R/http.log"), @("vm-client", "$R/background.log"),
                 @($GenVm, "$R/generator.log"))) {
  $name = Split-Path $p[1] -Leaf
  if (Pull $p[0] $p[1] $work) { Push "vm-controller" $work $name "$R/" | Out-Null }
}
# --- finalize trên controller
Remote "vm-controller" @"
cd $R && python3 $H/scenarios/common/finalize_run.py --run $RunId --groundtruth $R/$RunId.groundtruth.json \
  --telemetry $R/telemetry.csv --pcap /var/tmp/aiguard-runs/$RunId/openflow.pcap \
  --http $R/http.csv --dataplane $R/dataplane.csv --out $R/$RunId.csv > $R/finalize.log 2>&1; cat $R/finalize.log
"@ | ForEach-Object { Log $_ }

# --- tải về repo
$dest = Join-Path $repo "data\raw\vm-controller\sdn-ai\dataset\$cat\$scn\$RunId"
if (Test-Path -LiteralPath $dest) {   # bản cũ cùng RunId -> cất vào .runs-tmp (không trộn file cũ/mới)
  $old = Join-Path $repo ".runs-tmp\$RunId.dataset-bak-$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
  Move-Item -LiteralPath $dest $old; Log "Cất bản tải về cũ sang $old" }
New-Item -ItemType Directory -Force $dest | Out-Null
foreach ($f in "$RunId.csv", "telemetry.csv", "telemetry.log", "finalize.log") { Pull "vm-controller" "$R/$f" $dest | Out-Null }
foreach ($f in "$RunId.groundtruth.json", "dataplane.csv", "dataplane.log", "http.csv", "http.log", "background.log", "generator.log") {
  $l = Join-Path $work $f; if (Test-Path $l) { Copy-Item $l $dest -Force }
}
if ($Detector -ne "none") {
  $dd = Join-Path $dest "detector"; New-Item -ItemType Directory -Force $dd | Out-Null
  foreach ($f in "detector/events.jsonl", "detector/decisions.csv", "detector.log") { Pull "vm-controller" "$R/$f" $dd | Out-Null }
  Log ((Get-Content (Join-Path $dd "events.jsonl") -ErrorAction SilentlyContinue | Select-String '"state"|rule_' | Select-Object -First 8) -join " || ")
}
Copy-Item (Join-Path $work "orchestrate.log") $dest -Force
Log "DONE -> $dest"
