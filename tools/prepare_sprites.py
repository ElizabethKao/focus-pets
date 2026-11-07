"""Turns raw sprite art (art/raw/<pet>/*.png) into app-ready frames (pets/<pet>/).

For each pet it:
  1. lines every frame up on one ground line (lowest opaque pixel),
  2. centers every frame horizontally by its visual center of mass,
     so switching poses doesn't make the pet jitter,
  3. crops all frames to one shared canvas, symmetric around the center
     (so mirroring for "walk left" doesn't shift the pet), with room above
     the head for hearts and z's,
  4. writes meta.json (per-frame bounds) and icon.png (menu bar icon),
  5. renders docs/pets.png, a preview sheet of every pose.

Run it whenever you change the art:
    pip install Pillow
    python tools/prepare_sprites.py
"""
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "art" / "raw"
OUT = ROOT / "pets"
DOCS = ROOT / "docs"

FRAMES = ["idle_1", "blink", "walk_1", "walk_2", "walk_3", "walk_4", "happy_1",
          "eat_1", "eat_2", "sleep_1", "sleep_2", "held_1", "fall_1", "sign_1"]
ALPHA_THRESHOLD = 40   # pixels fainter than this count as empty when measuring
BOTTOM_PAD = 16        # space under the feet (room for the ground shadow)
TOP_ROOM = 100         # space above the tallest pose for hearts and z's
SIDE_PAD = 24
# frame -> (reference frame, target height ratio). Keeps redrawn frames at a consistent scale,
# e.g. blink flashes in over idle many times a minute, so any size difference reads as pulsing.
MATCH_SIZE = {"blink": ("idle_1", 1.0), "sign_1": ("idle_1", 1.02)}


def mask_of(im):
    return im.getchannel("A").point(lambda a: 255 if a > ALPHA_THRESHOLD else 0)


def center_of_mass_x(mask):
    cols = list(mask.resize((mask.width, 1), Image.BOX).tobytes())
    total = sum(cols)
    return sum(x * v for x, v in enumerate(cols)) / total


def match_size(im, ref, ratio=1.0):
    """Scale im so its opaque height is ratio x ref's."""
    h_im = mask_of(im).getbbox()
    h_ref = mask_of(ref).getbbox()
    k = ratio * (h_ref[3] - h_ref[1]) / (h_im[3] - h_im[1])
    if abs(k - 1) < 0.01:
        return im
    size = (round(im.width * k), round(im.height * k))
    scaled = im.resize(size, Image.LANCZOS)
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.paste(scaled, ((im.width - size[0]) // 2, (im.height - size[1]) // 2), scaled)
    return out


def fur_color(im):
    """Average color of the light (fur) pixels, ignoring outlines and blush."""
    from PIL import ImageStat
    rgb = im.convert("RGB")
    light = rgb.convert("L").point(lambda v: 255 if v > 215 else 0)
    solid = mask_of(im)
    from PIL import ImageChops
    m = ImageChops.multiply(light, solid)
    return ImageStat.Stat(rgb, m).mean


def _histogram_lut(src_hist, ref_hist):
    """Lookup table that maps src's value distribution onto ref's."""
    def cdf(h):
        total, run, out = sum(h), 0, []
        for v in h:
            run += v
            out.append(run / total)
        return out
    cs, cr = cdf(src_hist), cdf(ref_hist)
    lut, j = [], 0
    for v in range(256):
        while j < 255 and cr[j] < cs[v]:
            j += 1
        lut.append(j)
    return lut


def match_color(im, ref):
    """If im's fur color is off from ref's (a redrawn frame), histogram-match its colors to ref."""
    have, want = fur_color(im), fur_color(ref)
    gains = [w / h if h else 1 for w, h in zip(want, have)]
    if all(abs(g - 1) < 0.008 for g in gains):
        return im, gains
    m_im, m_ref = mask_of(im), mask_of(ref)
    chans = []
    for c_im, c_ref in zip(im.split()[:3], ref.split()[:3]):
        chans.append(c_im.point(_histogram_lut(c_im.histogram(m_im), c_ref.histogram(m_ref))))
    return Image.merge("RGBA", (*chans, im.getchannel("A"))), gains


def prepare(pet):
    src = RAW / pet
    frames = {}
    for name in FRAMES:
        path = src / f"{name}.png"
        if path.exists():
            frames[name] = Image.open(path).convert("RGBA")
    if "idle_1" not in frames:
        raise SystemExit(f"{pet}: idle_1.png is required")
    # Frames that flash in briefly over another frame must be the same size as it.
    for name, (base, ratio) in MATCH_SIZE.items():
        if name in frames and base in frames:
            frames[name] = match_size(frames[name], frames[base], ratio)
    for name in list(frames):
        if name != "idle_1":
            frames[name], gains = match_color(frames[name], frames["idle_1"])
            if any(abs(g - 1) >= 0.008 for g in gains):
                print(f"  {pet}/{name}: color matched to idle (gains {', '.join(f'{g:.3f}' for g in gains)})")

    # Place every frame on a big working canvas, aligned to a shared ground line and center.
    work = 1400
    ground, center = 1000, work // 2
    placed = {}
    for name, im in frames.items():
        m = mask_of(im)
        l, t, r, b = m.getbbox()
        cx = center_of_mass_x(m)
        dx, dy = round(center - cx), ground - b
        canvas = Image.new("RGBA", (work, work), (0, 0, 0, 0))
        canvas.paste(im, (dx, dy), im)
        placed[name] = canvas

    # Shared crop, symmetric around the center line
    boxes = {n: mask_of(c).getbbox() for n, c in placed.items()}
    half = max(max(center - b[0], b[2] - center) for b in boxes.values()) + SIDE_PAD
    top = min(b[1] for b in boxes.values()) - TOP_ROOM
    crop = (center - half, top, center + half, ground + BOTTOM_PAD)

    dest = OUT / pet
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    meta = {"size": [crop[2] - crop[0], crop[3] - crop[1]],
            "ground_y": ground - crop[1], "frames": {}}
    out_frames = {}
    for name, canvas in placed.items():
        im = canvas.crop(crop)
        im.save(dest / f"{name}.png", optimize=True)
        meta["frames"][name] = list(mask_of(im).getbbox())
        out_frames[name] = im
    (dest / "meta.json").write_text(json.dumps(meta, indent=1))

    # Menu bar icon: the idle pose's head and shoulders, squared up
    idle = out_frames["idle_1"]
    l, t, r, b = meta["frames"]["idle_1"]
    side = max(r - l, int((b - t) * 0.8))
    cx = (l + r) // 2
    icon = idle.crop((cx - side // 2, t - 4, cx + side // 2, t - 4 + side)).resize((128, 128), Image.LANCZOS)
    icon.save(dest / "icon.png")
    print(f"{pet}: {len(out_frames)} frames -> {dest.relative_to(ROOT)}  canvas {meta['size']}")
    return out_frames


def preview_sheet(all_frames):
    cell, label_h, gap, cols = 170, 26, 14, 7
    pets = list(all_frames)
    rows_per_pet = (len(FRAMES) + cols - 1) // cols
    title_h = 44
    w = gap + cols * (cell + gap)
    h = sum(title_h + rows_per_pet * (cell + label_h + gap) for _ in pets) + gap
    sheet = Image.new("RGBA", (w, h), (255, 248, 241, 255))
    d = ImageDraw.Draw(sheet)
    try:
        title_font = ImageFont.load_default(size=24)
        font = ImageFont.load_default(size=15)
    except TypeError:
        title_font = font = ImageFont.load_default()

    y = gap
    for pet in pets:
        d.text((gap + 4, y + 8), pet.capitalize(), font=title_font, fill=(138, 111, 158))
        y += title_h
        for i, name in enumerate(FRAMES):
            if name not in all_frames[pet]:
                continue
            r, c = divmod(i, cols)
            x0 = gap + c * (cell + gap)
            y0 = y + r * (cell + label_h + gap)
            d.rounded_rectangle([x0, y0, x0 + cell, y0 + cell + label_h], 16,
                                fill=(255, 255, 255), outline=(243, 220, 230), width=2)
            im = all_frames[pet][name].copy()
            im.thumbnail((cell - 16, cell - 16), Image.LANCZOS)
            sheet.alpha_composite(im, (x0 + (cell - im.width) // 2, y0 + (cell - im.height) // 2 + 4))
            tw = d.textlength(name, font=font)
            d.text((x0 + (cell - tw) / 2, y0 + cell - 2), name, font=font, fill=(156, 134, 174))
        y += rows_per_pet * (cell + label_h + gap)
    DOCS.mkdir(exist_ok=True)
    sheet.convert("RGB").save(DOCS / "pets.png", optimize=True)
    print("preview ->", (DOCS / "pets.png").relative_to(ROOT))


if __name__ == "__main__":
    results = {}
    for pet_dir in sorted(p for p in RAW.iterdir() if p.is_dir()):
        results[pet_dir.name] = prepare(pet_dir.name)
    # Peep first in the preview
    ordered = dict(sorted(results.items(), key=lambda kv: kv[0] != "peep"))
    preview_sheet(ordered)
