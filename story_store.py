"""
Turns a submitted story form into saved content: resizes/compresses any
uploaded photos, then publishes the article JSON plus those photos as one
commit via github_content.py.

If GITHUB_TOKEN isn't set (e.g. running the dev server locally without it
configured), saves land on the local disk instead so the admin portal is
still usable for trying things out -- they just won't survive a restart,
since Render's free tier has no persistent disk.
"""
import io
import json
import os
from pathlib import Path

from PIL import Image, ImageOps

import github_content

PROJECT_ROOT = Path(__file__).parent
MAX_IMAGE_WIDTH = 1600
JPEG_QUALITY = 82


def process_image(file_storage):
    """Takes a Werkzeug FileStorage from a file input and returns resized,
    compressed JPEG bytes, or None if no file was actually chosen."""
    if not file_storage or not file_storage.filename:
        return None
    image = Image.open(file_storage.stream)
    image = ImageOps.exif_transpose(image)  # respect phone photo orientation
    image = image.convert("RGB")
    if image.width > MAX_IMAGE_WIDTH:
        new_height = round(image.height * (MAX_IMAGE_WIDTH / image.width))
        image = image.resize((MAX_IMAGE_WIDTH, new_height), Image.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def save_story(doc, images, is_update=False):
    """doc: the fully-built article dict (matches content.py's schema).
    images: {repo_path: bytes} for any photos this save includes (cover +
    template-specific). Writes content/articles/<slug>.json plus those
    image files as one atomic publish.

    Returns (published, message). published is True once this is committed
    to GitHub (durable, triggers Render's auto-deploy); False means it only
    landed on local disk (dev mode, no GITHUB_TOKEN set)."""
    article_path = f"content/articles/{doc['slug']}.json"
    article_bytes = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    files = {article_path: article_bytes}
    files.update(images)

    if os.environ.get("GITHUB_TOKEN"):
        verb = "Update" if is_update else "Add"
        message = f"{verb} story: {doc['title']}"
        github_content.commit_files(files, message)
        return True, "Saved and pushed to GitHub — Render will redeploy (usually 1-2 min), then it's live."

    for path, content in files.items():
        full_path = PROJECT_ROOT / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
    return False, (
        "Saved locally. This is dev mode (no GITHUB_TOKEN configured) — "
        "set it in Render's environment variables so saves actually publish."
    )
