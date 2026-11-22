<#
deploy_detector.ps1 — Cài module phát hiện/giảm thiểu thời gian thực lên vm-controller.

  1. Python 3.11 + venv ~/aiguard-venv với ĐÚNG phiên bản thư viện lúc huấn luyện
     (mô hình joblib/pickle không đảm bảo đọc được giữa các phiên bản scikit-learn khác nhau)
  2. Chép code: ~/aiguard-app/build_dataset.py, ~/aiguard-app/realtime/{detector,odl}.py,
     ~/aiguard-app/models/detector_rf.joblib
  3. Kiểm tra import + nạp mô hình

  .\scenarios\orchestrate\deploy_detector.ps1            # cài/cập nhật
  .\scenarios\orchestrate\deploy_detector.ps1 -CodeOnly  # chỉ chép lại code + model
KHÔNG chạy trong lúc đang thu dữ liệu (cài gói làm CPU controller tăng -> sai số liệu).
#>
param([switch]$CodeOnly, [string]$Zone = "asia-southeast1-a", [string]$User = "anh2004zai_gmail_com")
$ErrorActionPreference = "Continue"
$gcloud = "C:\Users\NGOC TU\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$H = "/home/$User"
$PKGS = "pandas==3.1.1 numpy==2.4.6 scikit-learn==1.9.1 joblib==1.6.0 scipy==1.17.1"

function Log($m) { Write-Host ("{0:HH:mm:ss} {1}" -f (Get-Date), $m) }
function Remote([string]$script) {
  $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($script -replace "`r`n", "`n")))
  for ($i = 1; $i -le 4; $i++) {
    $out = & $gcloud compute ssh vm-controller --zone $Zone --tunnel-through-iap --quiet --command "echo $b64 | base64 -d | bash" 2>&1 | ForEach-Object { "$_" }
    if (($out -join "`n") -notmatch 'FATAL ERROR|Connection closed by remote|failed to connect to backend') { break }
    Start-Sleep 8
  }
  $out | Where-Object { $_ -notmatch 'WARNING|Invalid characters|At line|~~~|CategoryInfo|FullyQualifiedErrorId|NativeCommandError|^\s*$|private SSH key|troubleshoot|ecommendation|IAP tunneling|External IP' }
}
function Push([string]$localRel, [string]$dst) {
  Push-Location -LiteralPath $repo
  try {
    for ($i = 1; $i -le 4; $i++) {
      & $gcloud compute scp $localRel "vm-controller:$dst" --zone $Zone --tunnel-through-iap --quiet 2>&1 | Out-Null
      if ($LASTEXITCODE -eq 0) { Log "  chép $localRel"; return }
      Start-Sleep 5
    }
    Log "  CHÉP LỖI $localRel"
  } finally { Pop-Location }
}

if (-not $CodeOnly) {
  Log "Cài Python 3.11 + venv + thư viện (vài phút)..."
  Remote @"
set -e
if ! command -v python3.11 >/dev/null; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get -qq update >/dev/null
  sudo DEBIAN_FRONTEND=noninteractive apt-get -y -qq install python3.11 python3.11-venv >/dev/null
fi
[ -x $H/aiguard-venv/bin/python ] || python3.11 -m venv $H/aiguard-venv
$H/aiguard-venv/bin/python -m pip install -q --upgrade pip
$H/aiguard-venv/bin/python -m pip install -q $PKGS
$H/aiguard-venv/bin/python -c "import pandas, sklearn; print('VENV_OK pandas', pandas.__version__, 'sklearn', sklearn.__version__)"
"@ | ForEach-Object { Log "  $_" }
}
Remote "mkdir -p $H/aiguard-app/realtime $H/aiguard-app/models" | Out-Null
Push "src/build_dataset.py" "$H/aiguard-app/"
Push "src/realtime/detector.py" "$H/aiguard-app/realtime/"
Push "src/realtime/odl.py" "$H/aiguard-app/realtime/"
Push "models/detector_rf.joblib" "$H/aiguard-app/models/"
Remote @"
cd $H/aiguard-app/realtime && $H/aiguard-venv/bin/python -c "
import joblib, detector, build_dataset
b = joblib.load('$H/aiguard-app/models/detector_rf.joblib')
print('DEPLOY_OK model', type(b['model']).__name__, len(b['features']), 'features, k/n =', b['confirm_k'], b['confirm_n'])"
"@ | ForEach-Object { Log "  $_" }
