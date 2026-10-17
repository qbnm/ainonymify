"""Generate the ICO fallback using the same geometry/colors as the SVG logo."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

root = Path(__file__).resolve().parents[1]
svg = root / 'docs/assets/ainonymify-logo.svg'
(root / 'frontend/favicon.svg').write_bytes(svg.read_bytes())
fonts = [Path('/System/Library/Fonts/Supplemental/Georgia.ttf'),
         Path('/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf')]
font_path = next((p for p in fonts if p.exists()), None)
if font_path is None:
    raise RuntimeError('Install Georgia or DejaVu Serif to regenerate the ICO')
scale = 4
image = Image.new('RGBA', (128*scale, 128*scale))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((scale, scale, 127*scale, 127*scale), radius=24*scale,
                       fill='#172e34', outline='#637479', width=scale)
draw.text((64*scale, 88*scale), 'a', fill='#f5f6f2',
          font=ImageFont.truetype(str(font_path), 104*scale), anchor='ms')
image = image.resize((128, 128), Image.Resampling.LANCZOS)
image.save(root / 'frontend/favicon.ico', sizes=[(s, s) for s in (16, 32, 48, 64, 128)])
