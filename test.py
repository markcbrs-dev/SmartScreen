from pathlib import Path
from docx import Document
from PyPDF2 import PdfReader


def extract_docx(file_path):
    doc = Document(file_path)
    return "\n".join(paragraph.text for paragraph in doc.paragraphs)


def extract_pdf(file_path):
    reader = PdfReader(file_path)
    text = []

    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text.append(page_text)

    return "\n".join(text)


def extract_text(file_path):
    file_path = Path(file_path)

    if file_path.suffix.lower() == ".docx":
        return extract_docx(file_path)

    elif file_path.suffix.lower() == ".pdf":
        return extract_pdf(file_path)

    else:
        raise ValueError(
            f"Unsupported file type: {file_path.suffix}. Only .docx and .pdf are supported."
        )


def save_as_txt(input_file, output_file=None):
    text = extract_text(input_file)

    if output_file is None:
        output_file = Path(input_file).with_suffix(".txt")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"Text saved to: {output_file}")


if __name__ == "__main__":
    input_file = "Jose Lorenzo Faelman - Resume.pdf"  # Change to your file
    save_as_txt(input_file)