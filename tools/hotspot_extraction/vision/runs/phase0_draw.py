"""Phase 0 proof: draw the teacher's boxes (red) and the old bake's boxes (blue) on page 31.

Usage: phase0_draw.py [runs/phase0-agy-<call>.json]   (default: the gemini-3.6-flash-low call)"""
import json, re, sys
from PIL import Image, ImageDraw, ImageFont

ROOT = "../../../"
SRC = sys.argv[1] if len(sys.argv) > 1 else "runs/phase0-agy-flash36low.json"
reply = json.load(open(SRC))["response"]
m = re.search(r"```json\s*(.*?)```", reply, re.S)
boxes = json.loads(m.group(1) if m else reply)
json.dump(boxes, open("runs/phase0-page31.teacher.json", "w"), indent=1)

idx = json.load(open("runs/phase0-page31.index.json"))
W, H = idx["width_px"], idx["height_px"]
sx, sy = W / idx["width_pt"], H / idx["height_pt"]

img = Image.open("runs/phase0-page31.jpg").convert("RGB")
d = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
except OSError:
    font = ImageFont.load_default()

# teacher: [ymin,xmin,ymax,xmax] in 0-1000 -> pixels
for b in boxes:
    y0, x0, y1, x1 = b["box_2d"]
    r = [x0 * W / 1000, y0 * H / 1000, x1 * W / 1000, y1 * H / 1000]
    d.rectangle(r, outline=(220, 30, 30), width=4)
    d.text((r[0] + 6, r[1] + 4), f"T:{b.get('label')}", fill=(220, 30, 30), font=font)

# old bake: [x0,y0,x1,y1] in PDF points, y up -> pixels
regions = json.load(open(ROOT + "activities/books/0a3fbb41-8855-460f-af3d-41489a269851/regions.json"))
old = regions["pages"]["31"]["activities"]
for a in old:
    x0, y0, x1, y1 = a["rect"]
    r = [x0 * sx, H - y1 * sy, x1 * sx, H - y0 * sy]
    d.rectangle(r, outline=(30, 60, 220), width=3)
    d.text((r[2] - 60, r[1] + 4), f"O:{a['label']}", fill=(30, 60, 220), font=font)

img.save("runs/phase0-page31.png")
print("teacher boxes", len(boxes), "old boxes", len(old), "->", "runs/phase0-page31.png")
