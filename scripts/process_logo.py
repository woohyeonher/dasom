"""
scripts/process_logo.py
Remove the white/light background from dasom_logo.png by making those pixels
transparent, then save the result in-place and to frontend/public/favicon.png.

Algorithm: for each pixel, compute how "white" it is by taking the minimum RGB
channel value.  Pixels at or above `threshold` fade to fully transparent; pixels
in the transition band below that get a proportional alpha so anti-aliased edges
look clean rather than hard-cut.
"""

from pathlib import Path

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from PIL import Image

ROOT = Path(__file__).parent.parent
SRC  = ROOT / "frontend" / "src" / "assets" / "dasom_logo.png"
ICON = ROOT / "frontend" / "public" / "favicon.png"


def _remove_white_numpy(img: Image.Image, threshold: int, fade_width: int) -> Image.Image:
    data = np.array(img.convert("RGBA"), dtype=np.float32)
    r, g, b, a = data[:,:,0], data[:,:,1], data[:,:,2], data[:,:,3]

    # Whiteness: 255 = pure white, 0 = fully saturated
    whiteness = np.minimum(np.minimum(r, g), b)

    fade_start = threshold - fade_width
    # blend = 1.0 at whiteness==threshold (fully transparent)
    # blend = 0.0 at whiteness==fade_start (original alpha)
    blend = np.clip((whiteness - fade_start) / fade_width, 0.0, 1.0)
    data[:,:,3] = np.clip(a * (1.0 - blend), 0, 255)

    return Image.fromarray(data.astype(np.uint8), "RGBA")


def _remove_white_pure(img: Image.Image, threshold: int, fade_width: int) -> Image.Image:
    img = img.convert("RGBA")
    pixels = list(img.getdata())
    fade_start = threshold - fade_width
    new_pixels = []
    for r, g, b, a in pixels:
        whiteness = min(r, g, b)
        if whiteness >= threshold:
            new_pixels.append((r, g, b, 0))
        elif whiteness >= fade_start:
            blend = (whiteness - fade_start) / fade_width
            new_alpha = int(a * (1.0 - blend))
            new_pixels.append((r, g, b, new_alpha))
        else:
            new_pixels.append((r, g, b, a))
    img.putdata(new_pixels)
    return img


def remove_white_background(
    img: Image.Image,
    threshold: int = 200,
    fade_width: int = 20,
) -> Image.Image:
    """
    threshold  — pixels with min(R,G,B) >= this become fully transparent (0-255).
    fade_width — number of levels below threshold over which alpha fades in,
                 giving smooth anti-aliased edges.
    """
    if HAS_NUMPY:
        return _remove_white_numpy(img, threshold, fade_width)
    return _remove_white_pure(img, threshold, fade_width)


def main() -> None:
    backend = "numpy" if HAS_NUMPY else "pure Pillow"
    print(f"Using {backend} backend")

    print(f"Loading  {SRC}")
    img = Image.open(SRC)
    print(f"  mode={img.mode}  size={img.size}")

    result = remove_white_background(img)

    result.save(SRC, format="PNG")
    print(f"Saved -> {SRC}")

    result.save(ICON, format="PNG")
    print(f"Saved -> {ICON}")

    print("Done.")


if __name__ == "__main__":
    main()
