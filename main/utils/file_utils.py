import os
import logging
from typing import IO, Optional

logger = logging.getLogger(__name__)


def _read_pdf_from_fileobj(fileobj: IO) -> str:
    from PyPDF2 import PdfReader

    reader = PdfReader(fileobj)
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n".join(pages)


def _read_docx_from_fileobj(fileobj: IO) -> str:
    from docx import Document

    # python-docx expects a path or file-like object supporting .read()
    fileobj.seek(0)
    doc = Document(fileobj)
    paragraphs = [p.text for p in doc.paragraphs if p.text]
    return "\n".join(paragraphs)


def extract_text_from_resume(file, filename: Optional[str] = None) -> str:
    """Extract plain text from a resume file.

    Args:
        file: An open file-like object (supports read/seek) or a filesystem path (str / Path).
        filename: Optional filename to determine extension if ``file`` is file-like.

    Returns:
        Extracted text string (may be empty).

    Raises:
        ValueError: when file type is unsupported or parsing fails.
    """
    # support path strings
    if isinstance(file, (str, os.PathLike)):
        path = str(file)
        _, ext = os.path.splitext(path)
        ext = ext.lower()
        try:
            if ext == '.pdf':
                with open(path, 'rb') as fh:
                    return _read_pdf_from_fileobj(fh)
            elif ext in ('.docx',):
                with open(path, 'rb') as fh:
                    return _read_docx_from_fileobj(fh)
        except Exception as e:
            logger.exception('Failed to extract from path %s', path)
            raise ValueError(f'Error parsing file: {e}') from e

    # file-like object path
    # try to get extension from filename param, then attribute
    ext = None
    if filename:
        _, ext = os.path.splitext(filename)
    elif hasattr(file, 'name') and isinstance(file.name, str):
        _, ext = os.path.splitext(file.name)

    ext = (ext or '').lower()

    try:
        # ensure file-like is at start
        if hasattr(file, 'seek'):
            file.seek(0)

        if ext == '.pdf':
            return _read_pdf_from_fileobj(file)
        elif ext in ('.docx',):
            return _read_docx_from_fileobj(file)
        else:
            raise ValueError('Unsupported file type: %s' % ext)
    except Exception as e:
        logger.exception('Failed to extract from file-like (name=%s)', getattr(file, 'name', None))
        raise ValueError(f'Error parsing file-like object: {e}') from e
