# 密碼只從匿名stdin管道讀取，不出現在命令列、檔案或回傳結果。
$ErrorActionPreference = 'Stop'
try {
    $certificateRequest = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $certificateBytes = [System.IO.File]::ReadAllBytes($certificateRequest.path)
    $certificate = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2
    $certificate.Import($certificateBytes, $certificateRequest.password, [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet)
    @{ok=$true;has_private_key=$certificate.HasPrivateKey;not_before=$certificate.NotBefore.ToUniversalTime().ToString('o');not_after=$certificate.NotAfter.ToUniversalTime().ToString('o')} | ConvertTo-Json -Compress
    $certificate.Dispose()
} catch {
    $nativeException = $_.Exception.GetBaseException()
    @{ok=$false;hresult=('{0:X8}' -f $nativeException.HResult)} | ConvertTo-Json -Compress
} finally {
    $certificateRequest = $null
    $certificateBytes = $null
}
