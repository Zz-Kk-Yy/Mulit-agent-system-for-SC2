# 中文安全提问：勿用 Invoke-RestMethod 直接调 /api/ask（WinPS 5.x 易乱码），请用本脚本或 test_ask_api.ps1
param(
  [string]$ApiBase = "http://127.0.0.1:8011",
  [Parameter(Mandatory = $true)][string]$Question,
  [string]$SessionId = "default"
)

$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$bodyJson = (@{ question = $Question; session_id = $SessionId } | ConvertTo-Json -Depth 5 -Compress)
$resp = Invoke-WebRequest -Method Post `
  -Uri "$ApiBase/api/ask" `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($bodyJson))

$stream = $resp.RawContentStream
$stream.Position = 0
$reader = [System.IO.StreamReader]::new($stream, [System.Text.UTF8Encoding]::new($false), $true)
$jsonText = $reader.ReadToEnd()
$reader.Dispose()
$res = $jsonText | ConvertFrom-Json

Write-Output $res.answer
