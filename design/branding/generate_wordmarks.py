"""Regenerate portable Cinzel wordmark outlines (requires fonttools)."""
from pathlib import Path
import xml.etree.ElementTree as ET
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.pens.svgPathPen import SVGPathPen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)
font = TTFont(HERE / "Cinzel.ttf")
font = instantiateVariableFont(font, {"wght": 650}, inplace=False)
glyphs = font.getGlyphSet()
cmap = font.getBestCmap()
upem = font["head"].unitsPerEm

def lettering(root, text, x, baseline, size, tracking, ink, max_width):
    names = [cmap[ord(c)] for c in text]
    width = sum(glyphs[n].width for n in names) + tracking * (len(names) - 1)
    scale = min(size / upem, max_width / width)
    group = ET.SubElement(root, f"{{{NS}}}g", {"data-wordmark": "Cinzel", "fill": ink})
    cursor = 0
    for name in names:
        pen = SVGPathPen(glyphs)
        glyphs[name].draw(pen)
        if pen.getCommands():
            ET.SubElement(group, f"{{{NS}}}path", {
                "d": pen.getCommands(),
                "transform": f"translate({x + cursor * scale:.4f} {baseline}) scale({scale:.6f} {-scale:.6f})",
            })
        cursor += glyphs[name].width + tracking

for theme, ink, sub in [("dark", "#f8fafc", "#94a3b8"), ("light", "#172b46", "#52657c")]:
    path = ROOT / f"frontend/public/img/olympus-logo-{theme}.svg"
    root = ET.parse(path).getroot()
    for child in list(root):
        if child.tag == f"{{{NS}}}text" or child.get("data-wordmark"):
            root.remove(child)
    lettering(root, "OLYMPUS", 94, 44, 32, 50, ink, 196)
    lettering(root, "PRINT FARM", 96, 62, 9, 210, sub, 165)
    ET.ElementTree(root).write(path, encoding="unicode")
