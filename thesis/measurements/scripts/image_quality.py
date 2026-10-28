#!/usr/bin/env python3
"""Image quality of the JPEG product images against the PNG baseline, measured offline.

The format series (run_ab.sh --compare format) shows what JPEG saves in energy; this
script measures what it costs in image quality. It fetches every product image at the
measured size from the PNG container (8082) and the JPEG container (8083), exactly as a
client receives them, and compares each pair:

- SSIM on luma with the settings of Wang et al. (2004): 11x11 Gaussian window, sigma 1.5,
  K1 = 0.01, K2 = 0.03. 1.0 means identical.
- PSNR over RGB, in dB.
- Size of the decoded image file (not the base64 text), PNG and JPEG.

Product images are generated from a seed equal to the product id (CreatorRunner), so both
containers serve the same picture. The PNG images carry an alpha channel the JPEG ones do
not; the PNG is flattened on white, as the image service does before JPEG encoding, so the
comparison measures compression and not the background.

Needs Pillow, numpy and scikit-image (requirements-quality.txt). Run it in a container so
nothing has to be installed on the machine, from the repository root, with the stack up:

    docker run --rm --network host -u "$(id -u):$(id -g)" -e HOME=/tmp \\
        -v "$PWD/thesis/measurements:/m" -w /m/scripts python:3.12-slim sh -c \\
        "pip install --user -q -r requirements-quality.txt && python image_quality.py --sut localhost"

Writes data/image_quality/quality.csv (one row per product) and summary.json, and two
side-by-side figures to figures/: the product with the median SSIM and the one with the
lowest, each with a 3x enlargement of the area where the two differ most.
"""

import argparse
import base64
import csv
import io
import json
import statistics
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from skimage.metrics import peak_signal_noise_ratio, structural_similarity

HERE = Path(__file__).resolve().parent
ZOOM = 3
CROP = (96, 72)  # width, height of the enlarged area in source pixels


def fetch(base_url, product_id, size):
    """The product image as the service returns it, decoded from its data URI."""
    body = json.dumps({str(product_id): size}).encode()
    request = urllib.request.Request(f"{base_url}/rest/image/getProductImages", data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        uri = json.loads(response.read())[str(product_id)]
    header, _, payload = uri.partition(",")
    return header, base64.b64decode(payload)


def on_white(image):
    """RGB image, with any transparency composited on white as the service does for JPEG."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def compare(png, jpeg):
    reference, compressed = np.asarray(png), np.asarray(jpeg)
    ssim = structural_similarity(np.asarray(png.convert("L")), np.asarray(jpeg.convert("L")),
                                 gaussian_weights=True, sigma=1.5, use_sample_covariance=False,
                                 data_range=255)
    psnr = peak_signal_noise_ratio(reference, compressed, data_range=255)
    return float(ssim), float(psnr)


def worst_area(png, jpeg):
    """Top-left corner of the CROP-sized window where the luma differs most on average."""
    difference = np.abs(np.asarray(png.convert("L"), dtype=float)
                        - np.asarray(jpeg.convert("L"), dtype=float))
    width, height = CROP
    integral = np.pad(difference.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    sums = (integral[height:, width:] - integral[:-height, width:]
            - integral[height:, :-width] + integral[:-height, :-width])
    y, x = np.unravel_index(np.argmax(sums), sums.shape)
    return int(x), int(y)


def figure(png, jpeg, quality_label, path):
    """PNG and JPEG side by side, and below each the same area enlarged ZOOM times."""
    font = ImageFont.load_default(size=16)
    x, y = worst_area(png, jpeg)
    box = (x, y, x + CROP[0], y + CROP[1])
    zoomed = [image.crop(box).resize((CROP[0] * ZOOM, CROP[1] * ZOOM), Image.NEAREST)
              for image in (png, jpeg)]
    gap, label = 16, 24
    column = max(png.width, zoomed[0].width)
    canvas = Image.new("RGB", (2 * column + gap, label + png.height + gap + label + zoomed[0].height),
                       "white")
    draw = ImageDraw.Draw(canvas)
    for index, (image, enlarged, title) in enumerate(
            zip((png, jpeg), zoomed, ("PNG", f"JPEG, laatu {quality_label}"))):
        left = index * (column + gap)
        draw.text((left, 2), title, fill="black", font=font)
        canvas.paste(image, (left, label))
        draw.rectangle((left + x, label + y, left + x + CROP[0] - 1, label + y + CROP[1] - 1),
                       outline=(220, 0, 0), width=2)
        top = label + png.height + gap
        draw.text((left, top + 2), f"Suurennos {ZOOM}x", fill="black", font=font)
        canvas.paste(enlarged, (left, top + label))
    canvas.save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sut", default="10.10.10.1")
    parser.add_argument("--png-port", type=int, default=8082)
    parser.add_argument("--jpeg-port", type=int, default=8083)
    parser.add_argument("--first-product-id", type=int, default=7)
    parser.add_argument("--catalog-size", type=int, default=100)
    parser.add_argument("--size", default="400x310", help="the size the measurements request")
    parser.add_argument("--jpeg-quality-label", default="0,8",
                        help="as set by -Dimage.jpegQuality in the Compose file; for the figure")
    parser.add_argument("--out", default=str(HERE.parent / "data" / "image_quality"))
    parser.add_argument("--figures", default=str(HERE.parent / "figures"))
    args = parser.parse_args()

    png_url = f"http://{args.sut}:{args.png_port}/tools.descartes.teastore.image"
    jpeg_url = f"http://{args.sut}:{args.jpeg_port}/tools.descartes.teastore.image"
    out, figures = Path(args.out), Path(args.figures)
    out.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)

    rows, images = [], {}
    for product_id in range(args.first_product_id, args.first_product_id + args.catalog_size):
        png_header, png_bytes = fetch(png_url, product_id, args.size)
        jpeg_header, jpeg_bytes = fetch(jpeg_url, product_id, args.size)
        if "image/png" not in png_header or "image/jpeg" not in jpeg_header:
            raise SystemExit(f"product {product_id}: expected PNG and JPEG, got "
                             f"{png_header} and {jpeg_header}. Are the ports right?")
        png = on_white(Image.open(io.BytesIO(png_bytes)))
        jpeg = on_white(Image.open(io.BytesIO(jpeg_bytes)))
        if png.size != jpeg.size:
            raise SystemExit(f"product {product_id}: sizes differ, {png.size} and {jpeg.size}")
        ssim, psnr = compare(png, jpeg)
        images[product_id] = (png, jpeg)
        rows.append({"product_id": product_id, "png_bytes": len(png_bytes),
                     "jpeg_bytes": len(jpeg_bytes),
                     "size_ratio": round(len(png_bytes) / len(jpeg_bytes), 3),
                     "ssim": round(ssim, 5), "psnr_db": round(psnr, 3)})

    with (out / "quality.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    by_ssim = sorted(rows, key=lambda row: row["ssim"])
    median_row, lowest_row = by_ssim[len(by_ssim) // 2], by_ssim[0]
    ssims, psnrs = [row["ssim"] for row in rows], [row["psnr_db"] for row in rows]
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sut": args.sut, "png_port": args.png_port, "jpeg_port": args.jpeg_port,
        "size": args.size, "products": len(rows), "jpeg_quality": args.jpeg_quality_label,
        "ssim": {"median": statistics.median(ssims), "mean": statistics.mean(ssims),
                 "min": min(ssims), "max": max(ssims), "min_product": lowest_row["product_id"]},
        "psnr_db": {"median": statistics.median(psnrs), "mean": statistics.mean(psnrs),
                    "min": min(psnrs), "max": max(psnrs)},
        "bytes": {"png_total": sum(row["png_bytes"] for row in rows),
                  "jpeg_total": sum(row["jpeg_bytes"] for row in rows)},
        "figures": {"median_ssim_product": median_row["product_id"],
                    "lowest_ssim_product": lowest_row["product_id"]},
    }
    summary["bytes"]["ratio"] = round(summary["bytes"]["png_total"] / summary["bytes"]["jpeg_total"], 3)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    for name, row in (("median", median_row), ("lowest", lowest_row)):
        png, jpeg = images[row["product_id"]]
        figure(png, jpeg, args.jpeg_quality_label, figures / f"image_quality_{name}_ssim.png")

    print(f"{len(rows)} products at {args.size}")
    print(f"SSIM (luma): median {summary['ssim']['median']:.4f}, "
          f"min {summary['ssim']['min']:.4f} (product {lowest_row['product_id']}), "
          f"max {summary['ssim']['max']:.4f}")
    print(f"PSNR (RGB): median {summary['psnr_db']['median']:.2f} dB, "
          f"min {summary['psnr_db']['min']:.2f} dB")
    print(f"bytes: PNG {summary['bytes']['png_total']}, JPEG {summary['bytes']['jpeg_total']}, "
          f"ratio {summary['bytes']['ratio']}")
    print(f"wrote {out}/quality.csv, summary.json and {figures}/image_quality_*_ssim.png")


if __name__ == "__main__":
    main()
