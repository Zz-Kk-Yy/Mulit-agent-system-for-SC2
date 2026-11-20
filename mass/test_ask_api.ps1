param(
  [string]$ApiBase = "http://127.0.0.1:8011",
  [string]$Question = "Where is the enemy main force now?",
  [string]$SessionId = "test-1",
  [string]$OutFile = ".\ask_result.json"
)

$ErrorActionPreference = "Stop"

# ---- Force UTF-8 in current PowerShell session ----
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

Write-Host "== SwarmBrain Ask API Test ==" -ForegroundColor Cyan
Write-Host "API: $ApiBase"
Write-Host "Question: $Question"
Write-Host ""

# ---- 1) Health check ----
try {
  $health = Invoke-RestMethod -Method Get -Uri "$ApiBase/health"
  Write-Host "[OK] /health => $($health.status), llm_ready=$($health.llm_ready)" -ForegroundColor Green
}
catch {
  Write-Host "[FAIL] Health check failed: $($_.Exception.Message)" -ForegroundColor Red
  Write-Host "Please ensure ask_api_server is running, e.g.:" -ForegroundColor Yellow
  Write-Host "uvicorn ask_api_server:app --host 127.0.0.1 --port 8011 --reload"
  exit 1
}

# ---- 2) Ask API ----
$bodyObj = @{
  question = $Question
  session_id = $SessionId
}
$bodyJson = $bodyObj | ConvertTo-Json -Depth 5
$bodyUtf8 = [System.Text.Encoding]::UTF8.GetBytes($bodyJson)

try {
  $resp = Invoke-WebRequest -Method Post `
    -Uri "$ApiBase/api/ask" `
    -ContentType "application/json; charset=utf-8" `
    -Body $bodyUtf8

  # Force UTF-8 decode to avoid mojibake on Windows PowerShell 5.x
  $stream = $resp.RawContentStream
  $stream.Position = 0
  $reader = [System.IO.StreamReader]::new($stream, [System.Text.UTF8Encoding]::new($false), $true)
  $jsonText = $reader.ReadToEnd()
  $reader.Dispose()
  $res = $jsonText | ConvertFrom-Json
}
catch {
  Write-Host "[FAIL] /api/ask call failed: $($_.Exception.Message)" -ForegroundColor Red
  exit 1
}

# ---- 3) Print concise result ----
Write-Host ""
Write-Host "=== Answer ===" -ForegroundColor Cyan
Write-Host $res.answer
Write-Host ""
Write-Host "=== Meta ===" -ForegroundColor Cyan
Write-Host ("source      : {0}" -f $res.source)
Write-Host ("tool_traces : {0}" -f (($res.tool_traces -join ", ")))

# ---- 4) Save full JSON (UTF-8) ----
$res | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutFile -Encoding utf8
Write-Host ""
Write-Host "[OK] Result saved to: $OutFile" -ForegroundColor Green

