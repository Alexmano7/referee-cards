"""Read La Liga referee designations from the RFEF's official PDFs.

The RFEF publishes one article per matchday and day ("Estos son los árbitros para los
partidos del sábado ...") whose only content is a linked PDF, e.g.
https://rfef.es/sites/default/files/designaciones_1a_division_masculina_-_..._jornada_12_sabado_0.pdf

`uv run referees rfef-pdf <pdf url>` downloads it (rfef.es only) to data/raw/rfef/ (git-ignored)
and prints its text, so Claude can read the match -> referee pairings during a refresh.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import requests
from pypdf import PdfReader

from referees.config import DATA_DIR, USER_AGENT

RFEF_DIR = DATA_DIR / "raw" / "rfef"
ALLOWED_HOSTS = {"rfef.es", "www.rfef.es"}


def download_pdf(url: str, out_dir: Path = RFEF_DIR) -> Path:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc not in ALLOWED_HOSTS
        or not parsed.path.endswith(".pdf")
    ):
        raise ValueError(f"Only https PDF links on rfef.es are allowed, got: {url}")
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    if not resp.content.startswith(b"%PDF"):
        raise ValueError("The link did not return a PDF")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / Path(parsed.path).name
    path.write_bytes(resp.content)
    return path


def pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)
