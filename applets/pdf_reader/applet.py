from __future__ import annotations

from pathlib import Path

from eugene.core import AppletBase
from eugene.models import Attachment


class PdfReaderApplet(AppletBase):
    name = "pdf_reader"
    description = "PDF extraction handler."
    load = "eager"
    inject = "selective"
    supported_extensions = [".pdf"]

    async def handle_file(self, attachment_ref: str) -> Attachment | None:
        path = Path(attachment_ref)
        if not path.exists():
            return None
        content = await self.services.files._extract_pdf(path)
        return Attachment(original_filename=path.name, file_type="application/pdf", content=content, chunked=len(content) > 8_000)
