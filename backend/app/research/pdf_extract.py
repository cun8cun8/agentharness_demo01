"""This module is also copied into the sandbox and executed as a standalone script."""
import base64
import json
import sys
from pathlib import Path


def extract(path, language="eng"):
    import pymupdf
    pages = []
    figure_count = 0
    with pymupdf.open(path) as document:
        if document.needs_pass:
            raise ValueError("ENCRYPTED_PDF_NOT_SUPPORTED")
        if len(document) > 50:
            raise ValueError("PDF_PAGE_LIMIT_EXCEEDED")
        for index, page in enumerate(document):
            text = page.get_text()
            ocr = len(text.strip()) < 30
            if ocr:
                text = page.get_text(textpage=page.get_textpage_ocr(language=language, dpi=150, full=True))
            tables = [table.extract() for table in page.find_tables().tables][:10]
            figures = []
            for image in page.get_image_info()[:10]:
                if figure_count >= 10:
                    break
                rect = pymupdf.Rect(image["bbox"]) & page.rect
                if rect.is_empty or rect.width < 1 or rect.height < 1:
                    continue
                scale = min(1, 400 / max(rect.width, rect.height))
                png = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect).tobytes("png")
                figures.append({"bbox": list(rect), "png_base64": base64.b64encode(png).decode()})
                figure_count += 1
            pages.append({"page": index + 1, "text": text[:20000], "ocr": ocr, "tables": tables, "figures": figures})
    return {"pages": pages, "pdf_page_count": len(pages)}


def extract_isolated(path):
    import os
    import shutil
    from tempfile import TemporaryDirectory
    from app.config import get_settings
    from app.services.sandbox_runner import sandbox_runner
    settings = get_settings()
    shared = settings.sandbox_shared_workspace_root if sandbox_runner.backend in {"docker", "kubernetes"} else None
    if shared:
        Path(shared).mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="rf-pdf-", dir=shared) as directory:
        root = Path(directory)
        shutil.copyfile(path, root / "input.pdf")
        shutil.copyfile(__file__, root / "extract.py")
        result = sandbox_runner.run([sys.executable if sandbox_runner.backend == "local" else "python", "extract.py", os.getenv("RESEARCHFORGE_OCR_LANGUAGES", "eng")],
                                    cwd=root, workspace_root=root, timeout_seconds=180)
        output = root / "output.json"
        if result.returncode != 0 or not output.is_file() or output.is_symlink() or output.stat().st_size > 10_000_000:
            raise ValueError("PDF_EXTRACTION_FAILED")
        data = json.loads(output.read_text(encoding="utf-8"))
    return "\n".join(page["text"] for page in data["pages"]), data


if __name__ == "__main__":
    Path("output.json").write_text(json.dumps(extract("input.pdf", sys.argv[1] if len(sys.argv) > 1 else "eng")), encoding="utf-8")
