$processes = Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -in @('python.exe', 'node.exe')) -and (
        $_.CommandLine -match 'uvicorn main:app' -or
        $_.CommandLine -match 'ng serve' -or
        $_.CommandLine -match 'frontBillingOrganizer'
    )
}

$count = 0
foreach ($process in $processes) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    $count++
}

Write-Host "$count processo(s) do Billing Organizer encerrado(s)." -ForegroundColor Yellow
