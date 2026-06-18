from pathlib import Path

import fitz


pdf_path = Path(r"F:/Documents (F)/Dev/THESIS/smartscreen_django/SmartScreen/Marlou-Resume.pdf")
output_path = Path(r"F:/Documents (F)/Dev/THESIS/smartscreen_django/SmartScreen/.tmp/marlou-resume-pymupdf.txt")

with fitz.open(pdf_path) as pdf:
    text = "".join(page.get_text() for page in pdf)

output_path.write_text(text, encoding="utf-8")
print(output_path)