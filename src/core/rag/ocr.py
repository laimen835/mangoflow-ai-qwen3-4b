"""Local OCR, cached by file content and language; never sends documents away."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path


def recognize_pdf(body: bytes, executable: str, cache: Path, max_pages: int = 12) -> list[str]:
    import pymupdf
    executable = str(Path(executable).resolve())
    native_windows = not Path(executable).is_file() and os.name == "nt"
    if not Path(executable).is_file() and not native_windows:
        raise ValueError("OCR engine is not installed")
    digest = hashlib.sha256(body + b"rus+eng-v1").hexdigest()
    folder = cache / "ocr" / digest
    folder.mkdir(parents=True, exist_ok=True)
    texts = []
    with pymupdf.open(stream=body, filetype="pdf") as pdf:
        if len(pdf) > max_pages:
            raise ValueError(f"Scanned PDF exceeds the OCR limit of {max_pages} pages")
        for index, page in enumerate(pdf):
            target = folder / f"page-{index + 1}"
            text_file = target.with_suffix(".txt")
            if not text_file.exists():
                image_file = target.with_suffix(".png")
                # Cap rendering size for very large scanned calendar sheets.
                scale = min(2.5, (2400 if native_windows else 4200) / max(page.rect.width, page.rect.height))
                page.get_pixmap(matrix=pymupdf.Matrix(scale, scale)).save(str(image_file))
                command = (["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(Path(__file__).resolve().parents[3] / "scripts" / "windows-ocr.ps1"),
                    "-ImagePath", str(image_file.resolve()), "-OutputPath", str(text_file.resolve())]
                    if native_windows else [executable, str(image_file), str(target), "-l", "rus+eng", "--psm", "3"])
                result = subprocess.run(command,
                    capture_output=True, timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode:
                    raise ValueError("OCR failed; check installed rus/eng language data")
            texts.append(text_file.read_text(encoding="utf-8").strip())
    return texts
