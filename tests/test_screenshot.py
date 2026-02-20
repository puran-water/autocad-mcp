"""Tests for screenshot providers — no AutoCAD needed."""

import base64
import io

import ezdxf
import pytest

from autocad_mcp.screenshot import MatplotlibScreenshotProvider, NullScreenshotProvider


# ---------------------------------------------------------------------------
# NullScreenshotProvider
# ---------------------------------------------------------------------------


class TestNullProvider:
    def test_returns_none(self):
        provider = NullScreenshotProvider()
        assert provider.capture() is None

    def test_multiple_calls_return_none(self):
        provider = NullScreenshotProvider()
        for _ in range(5):
            assert provider.capture() is None


# ---------------------------------------------------------------------------
# MatplotlibScreenshotProvider
# ---------------------------------------------------------------------------


class TestMatplotlibProvider:
    def test_no_doc_returns_none(self):
        provider = MatplotlibScreenshotProvider()
        assert provider.capture() is None

    def test_empty_doc_renders(self):
        doc = ezdxf.new("R2013")
        provider = MatplotlibScreenshotProvider(doc)
        result = provider.capture()
        # Empty doc should still render (blank image)
        assert result is not None
        # Verify it's valid base64
        decoded = base64.b64decode(result)
        assert len(decoded) > 0
        # Verify PNG magic bytes
        assert decoded[:4] == b"\x89PNG"

    def test_doc_with_entities_renders(self):
        doc = ezdxf.new("R2013")
        msp = doc.modelspace()
        msp.add_line((0, 0), (100, 100))
        msp.add_circle((50, 50), 25)
        msp.add_lwpolyline([(0, 0), (50, 0), (50, 50), (0, 50)], close=True)

        provider = MatplotlibScreenshotProvider(doc)
        result = provider.capture()
        assert result is not None

        decoded = base64.b64decode(result)
        assert decoded[:4] == b"\x89PNG"
        # Image with entities should be larger than empty
        assert len(decoded) > 1000

    def test_doc_setter(self):
        provider = MatplotlibScreenshotProvider()
        assert provider.doc is None

        doc = ezdxf.new("R2013")
        provider.doc = doc
        assert provider.doc is doc

    def test_base64_roundtrip(self):
        """Encode to base64 and decode back, verify PNG structure."""
        doc = ezdxf.new("R2013")
        msp = doc.modelspace()
        msp.add_line((0, 0), (100, 0))

        provider = MatplotlibScreenshotProvider(doc)
        b64_str = provider.capture()
        assert b64_str is not None

        # Decode
        img_bytes = base64.b64decode(b64_str)

        # Verify PNG signature
        assert img_bytes[:8] == b"\x89PNG\r\n\x1a\n"

        # Re-encode and verify match
        re_encoded = base64.b64encode(img_bytes).decode("ascii")
        assert re_encoded == b64_str

    def test_image_dimensions_reasonable(self):
        """Verify rendered image has reasonable dimensions."""
        doc = ezdxf.new("R2013")
        msp = doc.modelspace()
        msp.add_line((0, 0), (100, 100))

        provider = MatplotlibScreenshotProvider(doc)
        b64_str = provider.capture()
        img_bytes = base64.b64decode(b64_str)

        # Parse PNG IHDR chunk to get dimensions
        # IHDR is always the first chunk after the 8-byte signature
        # Format: 4 bytes length, 4 bytes type ("IHDR"), 4 bytes width, 4 bytes height
        assert img_bytes[12:16] == b"IHDR"
        width = int.from_bytes(img_bytes[16:20], "big")
        height = int.from_bytes(img_bytes[20:24], "big")

        # At 150 DPI with 16x10 inch figsize, expect ~2400x1500
        assert 500 < width < 5000, f"Width {width} out of range"
        assert 500 < height < 3000, f"Height {height} out of range"

    def test_multiple_renders_consistent(self):
        """Rendering the same doc twice should produce same-sized output."""
        doc = ezdxf.new("R2013")
        msp = doc.modelspace()
        msp.add_circle((50, 50), 25)

        provider = MatplotlibScreenshotProvider(doc)
        r1 = provider.capture()
        r2 = provider.capture()

        # Both should succeed
        assert r1 is not None
        assert r2 is not None

        # Sizes should be very close (matplotlib may have minor non-determinism)
        s1 = len(base64.b64decode(r1))
        s2 = len(base64.b64decode(r2))
        assert abs(s1 - s2) < s1 * 0.1  # Within 10%
