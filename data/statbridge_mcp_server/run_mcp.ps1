python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host ".env 파일을 생성했습니다. KOSIS_API_KEY를 입력한 뒤 다시 실행하세요." -ForegroundColor Yellow
    exit 1
}
.\.venv\Scripts\python.exe .\server.py
