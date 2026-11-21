"""Build small real .docx and .pdf files for tests, with no extra dependencies."""
from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def para(text: str = "", *, style: str | None = None, hidden: str | None = None, page_break: bool = False) -> str:
    """One Word paragraph. `hidden` adds a run Word would not display; `page_break` starts a new page first."""
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    runs = '<w:r><w:br w:type="page"/></w:r>' if page_break else ""
    if text:
        runs += f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'
    if hidden:
        runs += f'<w:r><w:rPr><w:vanish/></w:rPr><w:t>{escape(hidden)}</w:t></w:r>'
    return f"<w:p>{ppr}{runs}</w:p>"


def table(rows: list[list[str]]) -> str:
    return "<w:tbl>" + "".join(
        "<w:tr>" + "".join(f"<w:tc>{para(c)}</w:tc>" for c in row) + "</w:tr>" for row in rows) + "</w:tbl>"


def make_docx(*blocks: str, document_xml: str | None = None) -> bytes:
    xml = document_xml or f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NS}><w:body>{"".join(blocks)}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def make_pdf_pages(*pages: str) -> bytes:
    """A valid PDF with one line of text per page."""
    n = len(pages)
    font = 3 + 2 * n
    objs = [b"<</Type/Catalog/Pages 2 0 R>>",
            ("<</Type/Pages/Kids[" + " ".join(f"{3 + 2 * i} 0 R" for i in range(n)) + f"]/Count {n}>>").encode()]
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
        objs.append(f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 400 200]/Contents {4 + 2 * i} 0 R/Resources<</Font<</F1 {font} 0 R>>>>>>".encode())
        objs.append(b"<</Length %d>>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer<</Root 1 0 R/Size {len(objs) + 1}>>\nstartxref\n{xref}\n%%EOF\n".encode()
