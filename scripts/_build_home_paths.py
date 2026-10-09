"""Replace the three wave Rectangles in HomeView.xaml with Path elements
carrying actual sine geometry. Run once; the resulting XAML is committed.
"""
import math
import pathlib

W = 1400
H = 100
CY = H / 2
SAMPLES = 240

def sine_path(A: float, period: float, phase: float) -> str:
    pts = []
    for i in range(SAMPLES + 1):
        x = i * W / SAMPLES
        y = CY + A * math.sin(2 * math.pi * x / period + phase)
        pts.append((x, y))
    d = f"M{pts[0][0]:.1f},{pts[0][1]:.1f} "
    for x, y in pts[1:]:
        d += f"L{x:.1f},{y:.1f} "
    return d.rstrip()

paths = {
    "Wave1": sine_path(20, 220, 0),
    "Wave2": sine_path(14, 9, math.pi / 3),
    "Wave3": sine_path(10, 320, math.pi / 2),
}

p = pathlib.Path(__file__).resolve().parents[1] / "ui/windows/Genie.Desktop/Views/HomeView.xaml"
t = p.read_text(encoding="utf-8")

import re
for i, name in enumerate(["Wave1", "Wave2", "Wave3"], start=1):
    pattern = rf'<Rectangle x:Name="{name}"[^>]*?StrokeEndLineCap="Round"/>'
    if not re.search(pattern, t, re.DOTALL):
        raise SystemExit(f"missing {name}")
    replacement = (
        f'<Path x:Name="{name}" Width="1400" Height="100" Stretch="Fill" '
        f'StrokeThickness="{1.6 - (i-1)*0.2:.1f}" '
        f'Stroke="{ "{" }StaticResource G.Wave{ "}" }" '
        f'Fill="Transparent" StrokeStartLineCap="Round" StrokeEndLineCap="Round" '
        f'Data="{paths[name]}"/>'
    ).replace("{ ", "{").replace(" }", "}")
    # Add the per-wave opacity/margin the rectangles had, on a wrapping <Canvas>.
    extras = ""
    if i == 2:
        extras = ' Margin="0,18,0,-18" Opacity="0.55"'
    elif i == 3:
        extras = ' Margin="0,-22,0,22" Opacity="0.45"'
    # Insert extras before the closing "/>"
    replacement = replacement[:-2] + extras + "/>"
    t = re.sub(pattern, replacement, t, count=1, flags=re.DOTALL)

p.write_text(t, encoding="utf-8", newline="")
print("replaced 3 rectangles with paths")