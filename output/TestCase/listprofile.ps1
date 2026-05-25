# 1. Gán danh sách tên vào biến (Dùng nháy đơn để giữ nguyên ký tự $)
$FolderList = @'
Hyundai_Optimize_001
Hyundai_Optimize_002
Hyundai_Optimize_003
Hyundai_Optimize_004
Hyundai_Optimize_005
Hyundai_Optimize_006
Hyundai_Optimize_007
Hyundai_Optimize_008
Hyundai_Optimize_009
Hyundai_Optimize_010
Hyundai_Optimize_011
Hyundai_Optimize_012
Hyundai_Optimize_013
Hyundai_Optimize_014
Hyundai_Optimize_015
Hyundai_Optimize_016
Hyundai_Optimize_017
Hyundai_Optimize_018
Hyundai_Optimize_019
Hyundai_Optimize_020
Hyundai_Optimize_021
Hyundai_Optimize_022
Hyundai_Optimize_023
Hyundai_Optimize_024
Hyundai_Optimize_025
Hyundai_Optimize_026
Hyundai_Optimize_027
Hyundai_Optimize_028
Hyundai_Optimize_029
Hyundai_Optimize_030
Hyundai_Optimize_031
Hyundai_Optimize_032
Hyundai_Optimize_033
Hyundai_Optimize_034
Hyundai_Optimize_035
Hyundai_Optimize_036
Hyundai_Optimize_037
Hyundai_Optimize_038
Hyundai_Optimize_039
'@ -split "`r?`n" # Sửa ở đây: Tách được cả chuẩn Windows (\r\n) lẫn Linux (\n)

# 2. Vòng lặp xử lý và tạo folder dựa trên LiteralPath
foreach ($line in $FolderList) {
    $name = $line.Trim()
    # Kiểm tra nếu dòng không trống VÀ không chỉ chứa khoảng trắng thì mới tạo
    if (-not [string]::IsNullOrWhiteSpace($name)) {
        New-Item -ItemType Directory -LiteralPath $name -Force | Out-Null
    }
}
Write-Host "Xong! Tat ca folder da duoc tao thanh cong." -ForegroundColor Green