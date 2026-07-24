from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from textwrap import wrap
from typing import Any


BASE_URL = "https://dispatches.thebluefernco.com"


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
    elif event_id == "event_a12dae614b86cfa9":
        headline = "ECU Health extends in-network access"
        location = "Greenville, North Carolina"
        category = "Temporary network-access extension"
        date_line = "Through August 6"
    else:
        headline = title
        location = f"{facility_name}, {city}, {state}"
        category = public_label
        date_line = effective_date
    headline_lines = _preview_lines(headline, width=36, limit=2) or [headline]
    alt_text = f"The Blue Fern Co. Care Line social card for {headline}"
    return {
        "event_id": event_id,
        "headline": headline,
        "headline_lines": headline_lines,
        "location": location,
        "category": category,
        "date_line": date_line,
        "alt_text": alt_text,
        "brand": "The Blue Fern Co.",
        "domain": "dispatches.thebluefernco.com",
        "image_url": f"{BASE_URL}/events/{event_id}/social-card.png",
    }


_SOCIAL_CARD_RENDER_SCRIPT = "\n".join(
    [
        "param(",
        "  [Parameter(Mandatory = $true)]",
        "  [string]$SpecPath,",
        "  [Parameter(Mandatory = $true)]",
        "  [string]$OutputPath",
        ")",
        "",
        "Add-Type -AssemblyName System.Drawing",
        "",
        "function New-RoundedRectPath {",
        "  param(",
        "    [int]$X,",
        "    [int]$Y,",
        "    [int]$Width,",
        "    [int]$Height,",
        "    [int]$Radius",
        "  )",
        "  $path = New-Object System.Drawing.Drawing2D.GraphicsPath",
        "  $diameter = $Radius * 2",
        "  $path.AddArc($X, $Y, $diameter, $diameter, 180, 90)",
        "  $path.AddArc($X + $Width - $diameter, $Y, $diameter, $diameter, 270, 90)",
        "  $path.AddArc($X + $Width - $diameter, $Y + $Height - $diameter, $diameter, $diameter, 0, 90)",
        "  $path.AddArc($X, $Y + $Height - $diameter, $diameter, $diameter, 90, 90)",
        "  $path.CloseFigure()",
        "  return $path",
        "}",
        "",
        "function Draw-Line {",
        "  param(",
        "    [System.Drawing.Graphics]$Graphics,",
        "    [string]$Text,",
        "    [System.Drawing.Font]$Font,",
        "    [System.Drawing.Brush]$Brush,",
        "    [int]$X,",
        "    [int]$Y",
        "  )",
        "  $Graphics.DrawString($Text, $Font, $Brush, [float]$X, [float]$Y)",
        "}",
        "",
        "$spec = Get-Content -Raw -LiteralPath $SpecPath | ConvertFrom-Json",
        "$bitmap = [System.Drawing.Bitmap]::new(1200, 630, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)",
        "$graphics = [System.Drawing.Graphics]::FromImage($bitmap)",
        "try {",
        "  $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias",
        "  $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic",
        "  $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality",
        "  $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit",
        "  $graphics.Clear([System.Drawing.ColorTranslator]::FromHtml('#EFE7DA'))",
        "",
        "  $outerPath = New-RoundedRectPath -X 56 -Y 56 -Width 1088 -Height 518 -Radius 28",
        "  $outerBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(194, 255, 253, 249))",
        "  $borderPen = New-Object System.Drawing.Pen([System.Drawing.ColorTranslator]::FromHtml('#D5E1EA'), 2)",
        "  $topBrush = New-Object System.Drawing.SolidBrush([System.Drawing.ColorTranslator]::FromHtml('#1E3F4F'))",
        "  $accentBrush = New-Object System.Drawing.SolidBrush([System.Drawing.ColorTranslator]::FromHtml('#D9E6F0'))",
        "  $deepBrush = New-Object System.Drawing.SolidBrush([System.Drawing.ColorTranslator]::FromHtml('#1E3F4F'))",
        "  $labelBrush = New-Object System.Drawing.SolidBrush([System.Drawing.ColorTranslator]::FromHtml('#1E3F4F'))",
        "  $headlineBrush = New-Object System.Drawing.SolidBrush([System.Drawing.ColorTranslator]::FromHtml('#1E3F4F'))",
        "  $metaBrush = New-Object System.Drawing.SolidBrush([System.Drawing.ColorTranslator]::FromHtml('#4E6B79'))",
        "  $brandBrush = New-Object System.Drawing.SolidBrush([System.Drawing.ColorTranslator]::FromHtml('#1E3F4F'))",
        "",
        "  $graphics.FillPath($outerBrush, $outerPath)",
        "  $graphics.DrawPath($borderPen, $outerPath)",
        "  $graphics.FillRectangle($topBrush, 56, 56, 1088, 12)",
        "  $graphics.FillEllipse($accentBrush, 972, 88, 120, 120)",
        "  $graphics.FillEllipse($deepBrush, 1008, 124, 48, 48)",
        "  $graphics.DrawLine((New-Object System.Drawing.Pen([System.Drawing.Color]::White, 8)), 1032, 124, 1032, 172)",
        "  $graphics.DrawLine((New-Object System.Drawing.Pen([System.Drawing.Color]::White, 8)), 1008, 148, 1056, 148)",
        "",
        "  $brandFont = New-Object System.Drawing.Font([System.Drawing.FontFamily]::GenericSansSerif, 22, [System.Drawing.FontStyle]::Bold)",
        "  $labelFont = New-Object System.Drawing.Font([System.Drawing.FontFamily]::GenericSansSerif, 24, [System.Drawing.FontStyle]::Bold)",
        "  $headlineFont = New-Object System.Drawing.Font([System.Drawing.FontFamily]::GenericSerif, 46, [System.Drawing.FontStyle]::Bold)",
        "  $metaFont = New-Object System.Drawing.Font([System.Drawing.FontFamily]::GenericSansSerif, 28, [System.Drawing.FontStyle]::Regular)",
        "  $metaBoldFont = New-Object System.Drawing.Font([System.Drawing.FontFamily]::GenericSansSerif, 28, [System.Drawing.FontStyle]::Bold)",
        "  $dateFont = New-Object System.Drawing.Font([System.Drawing.FontFamily]::GenericSansSerif, 24, [System.Drawing.FontStyle]::Regular)",
        "  $footerFont = New-Object System.Drawing.Font([System.Drawing.FontFamily]::GenericSansSerif, 20, [System.Drawing.FontStyle]::Regular)",
        "",
        "  Draw-Line -Graphics $graphics -Text $spec.brand -Font $brandFont -Brush $brandBrush -X 96 -Y 122",
        "  Draw-Line -Graphics $graphics -Text 'CARE LINE' -Font $labelFont -Brush $labelBrush -X 96 -Y 186",
        "",
        "  $y = 242",
        "  foreach ($line in $spec.headline_lines) {",
        "    Draw-Line -Graphics $graphics -Text $line -Font $headlineFont -Brush $headlineBrush -X 96 -Y $y",
        "    $y += 50",
        "  }",
        "",
        "  $y += 14",
        "  Draw-Line -Graphics $graphics -Text $spec.location -Font $metaFont -Brush $metaBrush -X 96 -Y $y",
        "  $y += 42",
        "  Draw-Line -Graphics $graphics -Text $spec.category -Font $metaBoldFont -Brush $headlineBrush -X 96 -Y $y",
        "  $y += 42",
        "  Draw-Line -Graphics $graphics -Text $spec.date_line -Font $dateFont -Brush $metaBrush -X 96 -Y $y",
        "",
        "  Draw-Line -Graphics $graphics -Text 'dispatches.thebluefernco.com' -Font $footerFont -Brush $metaBrush -X 96 -Y 540",
        "  Draw-Line -Graphics $graphics -Text 'Reviewed source record' -Font $footerFont -Brush $metaBrush -X 96 -Y 570",
        "  Draw-Line -Graphics $graphics -Text 'The Blue Fern Co.' -Font $footerFont -Brush $metaBrush -X 260 -Y 570",
        "",
        "  $bitmap.Save($OutputPath, [System.Drawing.Imaging.ImageFormat]::Png)",
        "}",
        "finally {",
        "  $graphics.Dispose()",
        "  $bitmap.Dispose()",
        "}",
    ]
)


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
                "-OutputPath",
                str(output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return output_path.read_bytes()
