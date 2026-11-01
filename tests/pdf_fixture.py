"""A tiny PDF writer for tests: real, valid PDF files (standard Helvetica font, one text stream per page) without any PDF library.

make_pdf([[line, line, ...], [], ...]) -> bytes. An empty list is a page with no text at all (a scan stand-in: it only draws a grey rectangle).
wrap(text, width) cuts a paragraph into lines like a typeset page would, optionally hyphenating one word across a line break.
"""
import textwrap


def _escape(text):
    return text.encode("latin-1", "replace").decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages, title=None):
    count = len(pages)
    bodies = [b"<< /Type /Catalog /Pages 2 0 R >>",
              f"<< /Type /Pages /Kids [{' '.join(f'{4 + 2 * i} 0 R' for i in range(count))}] /Count {count} >>".encode(),
              b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    for i, lines in enumerate(pages):
        page_number, content_number = 4 + 2 * i, 5 + 2 * i
        bodies.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_number} 0 R /Resources << /Font << /F1 3 0 R >> >> >>".encode())
        if lines:
            stream = "BT /F1 11 Tf 14 TL 56 740 Td " + " ".join(f"({_escape(line)}) Tj T*" for line in lines) + " ET"
        else:
            stream = "0.5 g 100 100 200 200 re f"
        data = stream.encode("latin-1")
        bodies.append(f"<< /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream")
    info = ""
    if title:
        bodies.append(f"<< /Title ({_escape(title)}) >>".encode())
        info = f" /Info {len(bodies)} 0 R"
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R{info} >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def wrap(text, width=88, hyphenate=None):
    """Lines of at most `width` characters. hyphenate="photosynthesis" splits that word across a line break the way a typeset book does."""
    if hyphenate and hyphenate in text:
        half = len(hyphenate) // 2
        text = text.replace(hyphenate, hyphenate[:half] + "-\n" + hyphenate[half:], 1)
        lines = []
        for part in text.split("\n"):
            lines += textwrap.wrap(part, width, break_long_words=False) if part.strip() else [""]
        # keep the hyphen at the end of its line
        return lines
    return textwrap.wrap(text, width, break_long_words=False)
