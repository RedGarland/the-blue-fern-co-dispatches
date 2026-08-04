from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from textwrap import wrap
from typing import Any


BASE_URL = "https://dispatches.thebluefernco.com"
SOCIAL_CARD_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "assets" / "care-line-social-card-template.png"


def _preview_lines(text: str, *, width: int, limit: int) -> list[str]:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return []
    lines: list[str] = []
    for paragraph in cleaned.split(" | "):
        lines.extend(wrap(paragraph, width=width) or [""])
        if len(lines) >= limit:
            break
    return lines[:limit]


def social_card_spec_for_event(
    *,
    event_id: str,
    title: str,
    facility_name: str,
    city: str,
    state: str,
    public_label: str,
    effective_date: str,
) -> dict[str, Any]:
    if event_id == "event_3b4ad4e528e48744":
        headline = "UCSF opens 8-bed pediatric neuroscience unit"
        location = "San Francisco, California"
        category = "Healthcare service expansion"
        date_line = "Opened July 22"
        social_description = (
            "UCSF opens an 8-bed pediatric neuroscience unit in San Francisco, expanding inpatient care for children."
        )
        headline_lines = ["UCSF opens 8-bed pediatric", "neuroscience unit"]
        headline_font_size_override = 60
        location_font_size_override = 26
    elif event_id == "event_a12dae614b86cfa9":
        headline = "ECU Health extends in-network access"
        location = "Greenville, North Carolina"
        category = "Temporary network-access extension"
        date_line = "Through August 6"
        social_description = (
            "ECU Health extends in-network access in Greenville through August 6 under a temporary agreement."
        )
        headline_lines = ["ECU Health extends in-", "network access"]
        headline_font_size_override = 60
        location_font_size_override = 26
    else:
        headline = title
        location = f"{facility_name}, {city}, {state}"
        category = public_label
        date_line = effective_date
        social_description = f"{headline} in {location}. {category}. {date_line}."
        headline_lines = _preview_lines(headline, width=28, limit=3) or [headline]
        headline_font_size_override = None
        location_font_size_override = None
    return {
        "event_id": event_id,
        "brand_name": "The Blue Fern Co.",
        "section_label": "CARE LINE",
        "headline": headline,
        "headline_lines": headline_lines,
        "location": location,
        "event_type_label": category,
        "date_label": date_line,
        "headline_font_size_override": headline_font_size_override,
        "location_font_size_override": location_font_size_override,
        "canonical_url": f"{BASE_URL}/events/{event_id}/",
        "social_description": social_description,
        "alt_text": f"The Blue Fern Co. Care Line social card for {headline}",
        "footer_text": "dispatches.thebluefernco.com",
        "icon_type": "stethoscope",
        "image_url": f"{BASE_URL}/events/{event_id}/social-card.png",
    }


_SOCIAL_CARD_RENDER_SCRIPT = r'''
param(
  [Parameter(Mandatory = $true)]
  [string]$SpecPath,
  [Parameter(Mandatory = $true)]
  [string]$OutputPath,
  [Parameter(Mandatory = $true)]
  [string]$TemplatePath
)

Add-Type -AssemblyName System.Drawing

function Get-ImageMetrics {
  param([System.Drawing.Bitmap]$Bitmap)
  $metrics = [ordered]@{}
  $metrics.Width = $Bitmap.Width
  $metrics.Height = $Bitmap.Height
  return $metrics
}

function Get-TextFont {
  param(
    [string]$FamilyName,
    [float]$Size,
    [System.Drawing.FontStyle]$Style
  )
  try {
    return New-Object System.Drawing.Font($FamilyName, $Size, $Style, [System.Drawing.GraphicsUnit]::Pixel)
  } catch {
    $fallbackFamily = switch ($FamilyName) {
      'Georgia' { 'Times New Roman' }
      'Segoe UI' { 'Arial' }
      default { [System.Drawing.FontFamily]::GenericSerif.Name }
    }
    return New-Object System.Drawing.Font($fallbackFamily, $Size, $Style, [System.Drawing.GraphicsUnit]::Pixel)
  }
}

function Draw-TextLine {
  param(
    [System.Drawing.Graphics]$Graphics,
    [string]$Text,
    [System.Drawing.Font]$Font,
    [System.Drawing.Brush]$Brush,
    [float]$X,
    [float]$Y
  )
  $Graphics.DrawString($Text, $Font, $Brush, $X, $Y)
}

$spec = Get-Content -Raw -LiteralPath $SpecPath | ConvertFrom-Json

$template = [System.Drawing.Bitmap]::FromFile($TemplatePath)
$bitmap = [System.Drawing.Bitmap]::new($template)
$template.Dispose()
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)

try {
  $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
  $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

  $tan = [System.Drawing.ColorTranslator]::FromHtml('#EFE7DA')
  $row = [System.Drawing.Color]::FromArgb(235, 218, 227, 233)
  $footer = [System.Drawing.Color]::FromArgb(214, 205, 218, 224)

  $headlineSize = if ($null -ne $spec.headline_font_size_override) { [float]$spec.headline_font_size_override } else { 60.0 }
  $headlineFont = Get-TextFont -FamilyName 'Georgia' -Size $headlineSize -Style ([System.Drawing.FontStyle]::Bold)
  $rowSize = if ($null -ne $spec.location_font_size_override) { [float]$spec.location_font_size_override } else { 26.0 }
  $rowFont = Get-TextFont -FamilyName 'Segoe UI' -Size $rowSize -Style ([System.Drawing.FontStyle]::Regular)

  $headlineBrush = New-Object System.Drawing.SolidBrush($tan)
  $rowBrush = New-Object System.Drawing.SolidBrush($row)
  $footerFont = Get-TextFont -FamilyName 'Segoe UI' -Size 18 -Style ([System.Drawing.FontStyle]::Regular)
  $footerBrush = New-Object System.Drawing.SolidBrush($footer)
  $footerBgBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, $bitmap.GetPixel(600, 340)))

  $headlineY = 250
  foreach ($line in $spec.headline_lines) {
    Draw-TextLine -Graphics $graphics -Text $line -Font $headlineFont -Brush $headlineBrush -X 92 -Y $headlineY
    $headlineY += 62
  }

  Draw-TextLine -Graphics $graphics -Text $spec.location -Font $rowFont -Brush $rowBrush -X 152 -Y 384
  Draw-TextLine -Graphics $graphics -Text $spec.event_type_label -Font $rowFont -Brush $rowBrush -X 152 -Y 442
  Draw-TextLine -Graphics $graphics -Text $spec.date_label -Font $rowFont -Brush $rowBrush -X 152 -Y 500

  $graphics.FillRectangle($footerBgBrush, 300, 540, 620, 34)
  $footerRect = [System.Drawing.RectangleF]::new(60.0, 546.0, 1080.0, 32.0)
  $footerFormat = New-Object System.Drawing.StringFormat
  $footerFormat.Alignment = [System.Drawing.StringAlignment]::Center
  $footerFormat.LineAlignment = [System.Drawing.StringAlignment]::Near
  $graphics.DrawString($spec.footer_text, $footerFont, $footerBrush, $footerRect, $footerFormat)

  $headlineFont.Dispose()
  $rowFont.Dispose()
  $headlineBrush.Dispose()
  $rowBrush.Dispose()
  $footerFont.Dispose()
  $footerBrush.Dispose()
  $footerBgBrush.Dispose()
  $footerFormat.Dispose()

  $bitmap.Save($OutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
}
finally {
  $graphics.Dispose()
  $bitmap.Dispose()
}
'''


def render_social_card_png_bytes(spec: dict[str, Any]) -> bytes:
    with tempfile.TemporaryDirectory(prefix="bluefern-care-line-card-") as temp_dir:
        temp_root = Path(temp_dir)
        spec_path = temp_root / "social-card.json"
        script_path = temp_root / "render-social-card.ps1"
        output_path = temp_root / "social-card.png"
        spec_path.write_text(json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        script_path.write_text(_SOCIAL_CARD_RENDER_SCRIPT, encoding="utf-8")
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                "-SpecPath",
                str(spec_path),
                "-TemplatePath",
                str(SOCIAL_CARD_TEMPLATE_PATH),
                "-OutputPath",
                str(output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return output_path.read_bytes()
