from app.tools.file_tool import _apply_unified_patch


def test_fallback_keeps_following_source_line_separate_without_diff_terminator(tmp_path):
    source = tmp_path / "value.py"
    source.write_bytes(b"value = 0\r\nnext_value = 2\r\nlast_value = 3\r\n")
    patch = "--- a/value.py\n+++ b/value.py\n@@ -1,2 +1,2 @@\n-value = 0\n+value = 1\n next_value = 2"
    changed_files, changed_lines, method = _apply_unified_patch(tmp_path, patch)
    assert (changed_files, changed_lines, method) == (1, 2, "fallback")
    assert source.read_bytes() == b"value = 1\r\nnext_value = 2\r\nlast_value = 3\r\n"
