import pytest

from app.tools.file_tool import _apply_unified_patch_fallback


def test_patch_relocates_only_unique_complete_context(tmp_path):
    source = tmp_path / "sample.py"
    source.write_bytes(b"# heading\r\nsubtotal = 200\r\ntotal = subtotal * 9\r\nprint(total)\r\n")
    patch = "--- a/sample.py\n+++ b/sample.py\n@@ -1,3 +1,3 @@\n subtotal = 200\n-total = subtotal * 9\n+total = subtotal * 1.08\n print(total)\n"
    assert _apply_unified_patch_fallback(tmp_path, patch) == (1, 2)
    assert source.read_bytes() == b"# heading\r\nsubtotal = 200\r\ntotal = subtotal * 1.08\r\nprint(total)\r\n"


def test_patch_rejects_ambiguous_context_without_writing(tmp_path):
    source = tmp_path / "sample.py"
    original = b"heading\nold\nold\n"
    source.write_bytes(original)
    patch = "--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n-old\n+new\n"
    with pytest.raises(ValueError, match="PATCH_CONTEXT_MISMATCH"):
        _apply_unified_patch_fallback(tmp_path, patch)
    assert source.read_bytes() == original
