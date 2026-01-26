'''

yung program na nagawa ko na 'to is nakakabasa ng PDF and .docx files pero sa terminal palang; binabasa ni Python yung
contents ng files sa terminal. This might be a good starting base sa pag-accept natin ng resumes sa program :3

ang gamit ko pala na IDE is " PyCharm Community Edition 2024.1.3 "

sabi lang/ping lang kayo guys if ever sa gc for updates and if may ma-hhelp pa me, i'll get back to it as fast
as I could hehe :3

- Lars

'''

import os
import tkinter as tk
from tkinter import filedialog # we need this para mag-open ng 'My Documents' yung program, pang select ng file
from PyPDF2 import PdfReader  # PDF reader, run 'pip install PyPDF2 python-docx' sa terminal ng IDE niyo para ma-install and magamit to :3
from docx import Document  # .docx reader, if na-install niyo yung codeline sa taas



def accept_resume(file_path):

    if not os.path.exists(file_path):
        print("File not found!")

        return

    _, ext = os.path.splitext(file_path) # pangkuha ng file extension
    ext = ext.lower()

    try:
        #pang-read ng PDF and pang-print sa terminal
        if ext == '.pdf':
            reader = PdfReader(file_path)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"

            print("Extracted text from PDF:")
            print(text)

        #pang-read ng docx and pang-print sa terminal
        elif ext == '.docx':
            doc = Document(file_path)
            text = ""
            for para in doc.paragraphs:
                text += para.text + "\n"

            print("Extracted text from .docx:")
            print(text)

        else:
            print("Error: Unsupported! Please select PDF or .docx files only.")

    except Exception as e:

        print(f"An error occurred while reading the file! {e}")


# main (reader)
if __name__ == "__main__":
    file_path = filedialog.askopenfilename(title="Please select a document: ")

    accept_resume(file_path)