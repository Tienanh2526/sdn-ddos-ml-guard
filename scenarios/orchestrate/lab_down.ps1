<#
lab_down.ps1 — Tắt (STOP) cả 8 VM để tiết kiệm credit khi không thu dữ liệu.
Ổ đĩa và dữ liệu trên VM được GIỮ NGUYÊN (stop, không phải delete). Khi bật lại: lab_up.ps1.
Mất khi tắt (lab_up.ps1 tự dựng lại): rule reactive trên leaf, dịch vụ UDP echo trên vm-web.
Lưu ý: IP ngoài tạm thời (ephemeral) của controller/leaf có thể đổi sau khi bật lại — không ảnh
hưởng vì mọi thao tác đi qua IAP + IP nội bộ.
#>
param([string]$Zone = "asia-southeast1-a")
$gcloud = "C:\Users\NGOC TU\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$VMS = "vm-controller", "vm-spine1", "vm-spine2", "vm-leaf1", "vm-leaf2", "vm-web", "vm-client", "vm-attacker"
& $gcloud compute instances stop @VMS --zone $Zone 2>&1 | Where-Object { "$_" -notmatch 'Invalid characters|At line|~~~|CategoryInfo|FullyQualified|NativeCommandError' }
& $gcloud compute instances list --format="table(name,status)" 2>&1 | Where-Object { "$_" -notmatch 'Invalid characters|At line|~~~|CategoryInfo|FullyQualified|NativeCommandError' }
