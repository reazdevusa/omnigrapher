# Kill any Celery worker processes that are NOT running from the project virtual environment.
Get-CimInstance Win32_Process -Filter "Name='python.exe' AND CommandLine LIKE '%celery%'" |
    Where-Object { $_.CommandLine -notlike '*.venv*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
