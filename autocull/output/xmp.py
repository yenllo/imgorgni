"""
XMP Sidecar Generation — Adobe Lightroom integration for Autocull.

Creates XMP sidecar files (.xmp) next to the images so that
Adobe Lightroom (and other XMP-aware tools) display them with a
configurable Rating and Color Label.

Default: Rating=1, Label=Red (the "reject" convention).

Handles existing XMP files by merging attributes into the existing
``rdf:Description`` element, preserving any user-set metadata.

Uses only Python stdlib: ``xml.etree.ElementTree``, ``pathlib``, ``os``.
"""

import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional


# ── XML Namespace constants ──────────────────────────────────────────────

NS_X = "adobe:ns:meta/"
NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
NS_XMP = "http://ns.adobe.com/xap/1.0/"

# Register namespaces so ElementTree uses the correct prefixes on write
ET.register_namespace("x", NS_X)
ET.register_namespace("rdf", NS_RDF)
ET.register_namespace("xmp", NS_XMP)


# ── XMP Template ─────────────────────────────────────────────────────────

_XMP_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
      xmlns:xmp="http://ns.adobe.com/xap/1.0/"
      xmp:Rating="{rating}"
      xmp:Label="{label}">
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
"""


def generate_xmp_sidecar(
    image_path: str,
    rating: int = 1,
    label: str = "Red",
) -> str:
    """Create or update an XMP sidecar file for the given image.

    The sidecar is placed next to the image, *replacing* the extension
    (``DSC00001.ARW`` → ``DSC00001.xmp``). That is Adobe's convention:
    Lightroom ignores ``DSC00001.ARW.xmp`` (the darktable/RawTherapee style),
    so appending would write files nobody reads.

    If the sidecar already exists, the Rating and Label attributes are
    merged into its existing ``rdf:Description`` element without
    destroying other metadata.  If no ``rdf:Description`` is found the
    file is rewritten from the template.

    Args:
        image_path: Path to the source image file.
        rating: XMP Rating value (1-5). Default ``1`` (reject).
        label: XMP Label string (``"Red"``, ``"Yellow"``, etc.).

    Returns:
        Absolute path of the created/updated ``.xmp`` file.

    Raises:
        FileNotFoundError: If *image_path* does not exist.
        OSError: If the sidecar cannot be written (permissions, etc.).
        ValueError: If *rating* is outside the 0-5 range.
    """
    img = Path(image_path).resolve()
    if not img.is_file():
        raise FileNotFoundError(f"Image file not found: {img}")

    if not (0 <= rating <= 5):
        raise ValueError(f"XMP Rating must be 0-5, got {rating}")

    xmp_path = img.with_suffix(".xmp")

    if xmp_path.is_file():
        _merge_xmp(xmp_path, rating, label)
    else:
        _write_fresh_xmp(xmp_path, rating, label)

    return str(xmp_path)


def generate_xmp_batch(
    image_paths: list[str],
    rating: int = 1,
    label: str = "Red",
) -> list[str]:
    """Generate XMP sidecars for multiple images.

    Convenience wrapper around :func:`generate_xmp_sidecar` that
    collects results and reports errors without aborting the batch.

    Args:
        image_paths: List of image file paths.
        rating: XMP Rating value (1-5). Default ``1``.
        label: XMP Label string. Default ``"Red"``.

    Returns:
        List of absolute paths for successfully created/updated XMP files.
        Failed paths are silently skipped (errors are printed to stderr).
    """
    created: list[str] = []
    for path in image_paths:
        try:
            xmp = generate_xmp_sidecar(path, rating=rating, label=label)
            created.append(xmp)
        except (FileNotFoundError, OSError, ValueError) as exc:
            # Log but don't abort the batch
            import sys
            print(
                f"[WARNING] XMP generation failed for {path}: {exc}",
                file=sys.stderr,
            )
    return created


# ── Private Helpers ──────────────────────────────────────────────────────


def _write_fresh_xmp(xmp_path: Path, rating: int, label: str) -> None:
    """Write a new XMP sidecar file from the template.

    Args:
        xmp_path: Destination path for the XMP file.
        rating: XMP Rating value.
        label: XMP Label string.
    """
    content = _XMP_TEMPLATE.format(rating=rating, label=label)
    xmp_path.write_text(content, encoding="utf-8")


def _merge_xmp(xmp_path: Path, rating: int, label: str) -> None:
    """Merge Rating and Label into an existing XMP sidecar.

    Parses the existing XML, locates the first ``rdf:Description``
    element, and sets (or overwrites) the ``xmp:Rating`` and
    ``xmp:Label`` attributes.  If the XML structure is unexpected
    the file is rewritten from scratch.

    Args:
        xmp_path: Path to the existing XMP file.
        rating: XMP Rating value to set.
        label: XMP Label string to set.
    """
    try:
        tree = ET.parse(xmp_path)
        root = tree.getroot()

        # Find all rdf:Description elements (could be nested)
        descriptions = root.iter(f"{{{NS_RDF}}}Description")
        description = next(descriptions, None)

        if description is not None:
            # Set the xmp attributes
            description.set(f"{{{NS_XMP}}}Rating", str(rating))
            description.set(f"{{{NS_XMP}}}Label", label)

            # Write back with XML declaration
            tree.write(
                str(xmp_path),
                encoding="unicode",
                xml_declaration=True,
            )
            # ElementTree omits the XML declaration encoding when using
            # ``encoding="unicode"``. Re-read and patch the declaration
            # to include ``encoding="UTF-8"`` for Lightroom compatibility.
            _patch_xml_declaration(xmp_path)
        else:
            # No Description element found — rewrite from template
            _write_fresh_xmp(xmp_path, rating, label)

    except ET.ParseError:
        # Corrupt XML — overwrite with a clean template
        _write_fresh_xmp(xmp_path, rating, label)


def _patch_xml_declaration(xmp_path: Path) -> None:
    """Ensure the XML declaration includes ``encoding="UTF-8"``.

    ElementTree's ``write(encoding="unicode")`` produces
    ``<?xml version='1.0' ?>`` without an encoding attribute.
    Lightroom expects ``encoding="UTF-8"`` in the declaration.

    Args:
        xmp_path: Path to the XMP file to patch.
    """
    content = xmp_path.read_text(encoding="utf-8")
    # Normalise declaration to what Lightroom expects
    for old in (
        "<?xml version='1.0' ?>",
        "<?xml version='1.0'?>",
        '<?xml version="1.0" ?>',
        '<?xml version="1.0"?>',
    ):
        if content.startswith(old):
            content = '<?xml version="1.0" encoding="UTF-8"?>' + content[len(old):]
            break
    xmp_path.write_text(content, encoding="utf-8")


# ── Self-Test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile
    import shutil

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake image file
        fake_img = os.path.join(tmpdir, "DSC00001.ARW")
        Path(fake_img).touch()

        # 1. Generate fresh XMP
        xmp = generate_xmp_sidecar(fake_img, rating=1, label="Red")
        assert os.path.basename(xmp) == "DSC00001.xmp", xmp  # Lightroom no lee .ARW.xmp
        print(f"[OK] Fresh XMP created: {xmp}")
        content = Path(xmp).read_text(encoding="utf-8")
        assert 'xmp:Rating="1"' in content, "Rating not found in XMP"
        assert 'xmp:Label="Red"' in content, "Label not found in XMP"
        print(f"     Content preview:\n{content[:300]}")

        # 2. Merge into existing XMP (update rating)
        xmp2 = generate_xmp_sidecar(fake_img, rating=3, label="Yellow")
        print(f"\n[OK] Merged XMP updated: {xmp2}")
        content2 = Path(xmp2).read_text(encoding="utf-8")
        assert "3" in content2, "Updated rating not found"
        assert "Yellow" in content2, "Updated label not found"
        print(f"     Content preview:\n{content2[:300]}")

        # 3. Batch generation
        fake_img2 = os.path.join(tmpdir, "DSC00002.ARW")
        Path(fake_img2).touch()
        results = generate_xmp_batch([fake_img, fake_img2], rating=1, label="Red")
        print(f"\n[OK] Batch XMP: {len(results)} files created")
        assert len(results) == 2

        # 4. Batch with a bad path (should warn, not crash)
        results_partial = generate_xmp_batch(
            [fake_img, "/nonexistent/photo.arw"], rating=1, label="Red"
        )
        print(f"[OK] Batch with bad path: {len(results_partial)}/2 succeeded")
        assert len(results_partial) == 1

        # 5. Invalid rating
        try:
            generate_xmp_sidecar(fake_img, rating=10)
            assert False, "Should have raised ValueError"
        except ValueError:
            print("[OK] Invalid rating correctly rejected")

    print("\nAll XMP tests passed.")
