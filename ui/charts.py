"""
PhotoVault - ui/charts.py
Gráficos SVG (sin dependencias de Qt, para poder testearlos).
"""

import html
import logging

logger = logging.getLogger(__name__)


def build_bar_chart_svg(data: list[tuple], color: str) -> tuple[str, int]:
    """SVG de barras horizontales; devuelve (svg, alto). Los textos se escapan."""
    max_val  = max(v for _, v in data)
    bar_h    = 22
    gap      = 6
    label_w  = 80
    chart_w  = 460
    height   = len(data) * (bar_h + gap) + 10
    svg_w    = label_w + chart_w + 60

    bars = []
    for i, (key, val) in enumerate(data):
        y    = 5 + i * (bar_h + gap)
        fill = int(val / max_val * chart_w) if max_val else 0
        # Recortar ANTES de escapar para no partir una entidad como "&amp;"
        key_str = html.escape(str(key)[:12])
        bars.append(
            f'<text x="{label_w - 6}" y="{y + bar_h - 6}" '
            f'text-anchor="end" fill="#8888AA" font-size="11">{key_str}</text>'
            f'<rect x="{label_w}" y="{y}" width="{fill}" height="{bar_h}" '
            f'rx="4" fill="{color}99"/>'
            f'<text x="{label_w + fill + 6}" y="{y + bar_h - 6}" '
            f'fill="#CCC" font-size="11">{val:,}</text>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{svg_w}" height="{height}">'
        f'<rect width="{svg_w}" height="{height}" fill="#13131F" rx="8"/>'
        + "".join(bars) +
        "</svg>"
    )
    return svg, height
