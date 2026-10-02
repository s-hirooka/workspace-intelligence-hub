$ErrorActionPreference = "Stop"
$modelDirectory = Join-Path $PSScriptRoot "..\models"
New-Item -ItemType Directory -Force -Path $modelDirectory | Out-Null
$models = @{
  "face_detection_yunet_2023mar.onnx" = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
  "face_recognition_sface_2021dec.onnx" = "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
}
foreach ($name in $models.Keys) {
  $destination = Join-Path $modelDirectory $name
  if (Test-Path -LiteralPath $destination) {
    Write-Host "Already exists: $destination"
    continue
  }
  Write-Host "Downloading $name..."
  Invoke-WebRequest -Uri $models[$name] -OutFile $destination
}
Write-Host "Image models are ready."