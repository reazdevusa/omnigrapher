# Clean up any leftover uvicorn or celery Python processes that are not part of a managed service run.
Get-CimInstance Win32_Process -Filter "Name='uvicorn.exe' OR (Name='python.exe' AND CommandLine LIKE '%uvicorn%')" |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Get-CimInstance Win32_Process -Filter "Name='python.exe' AND CommandLine LIKE '%celery%'" |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
