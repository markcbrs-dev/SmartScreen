"""
main/services/resume_parser.py — Extracts structured data from uploaded resume files.

OVERVIEW
--------
ResumeParser.parse() is the main entry point.
It accepts a file-like object (PDF or DOCX) and returns a ResumeProfile dataclass.

EXTRACTION PIPELINE
-------------------
  1. extract_text()      — reads raw text from PDF (pdfplumber → PyPDF2 fallback) or DOCX
  2. extract_email()     — regex: finds first valid email address
  3. extract_phone()     — regex: finds first valid phone number
  4. extract_skills()    — keyword match against common_skills vocabulary
  5. extract_education() — keyword-line heuristic (looks for university, degree, etc.)
  6. extract_experience()— keyword-line heuristic (looks for engineer, manager, etc.)
  7. extract_entities()  — spaCy NER (ORG, GPE, PERSON) if SMARTSCREEN_ENABLE_SPACY=1
                           Falls back to _guess_name() from the first 8 lines.

HOW TO MODIFY
-------------
  Add a new skill         → add it to _load_common_skills() list
                            Keep in sync with _SKILL_VOCAB in matcher.py
  Change education keywords → edit the `keywords` list in extract_education()
  Change experience keywords→ edit the `keywords` list in extract_experience()
  Enable spaCy NER        → set env var SMARTSCREEN_ENABLE_SPACY=1 and install en_core_web_sm
  Change NLP model        → pass nlp_model='...' to ResumeParser() in recruitment.py
  Change PDF extraction   → edit _extract_text_from_pdf() (primary: pdfplumber, fallback: PyPDF2)
  Change DOCX extraction  → edit _extract_text_from_docx()
  Increase name-guessing  → edit _guess_name() (currently scans first 8 lines)

DEBUG
-----
  No text extracted?    → check that pdfplumber and PyPDF2 are installed; check resume file format
  Skills always empty?  → check _load_common_skills() and that resume has recognisable keywords
  Name always None?     → check _guess_name(); resume may not have the name on a clean first line
  spaCy not running?    → check SMARTSCREEN_ENABLE_SPACY env var and en_core_web_sm installation
"""

import importlib
import logging
import os
import re
from typing import IO, Optional

from .entities import ResumeProfile
from .skills import (
    extract_canonical_skills,
    get_canonical_skills,
    normalize_education_entries,
    normalize_list_text,
    normalize_location_text,
)

logger = logging.getLogger(__name__)

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    from PyPDF2 import PdfReader
except ImportError:
    PdfReader = None

try:
    from docx import Document
except ImportError:
    Document = None

try:
    spacy = importlib.import_module('spacy')
except ImportError:
    spacy = None


ENABLE_SPACY = os.environ.get('SMARTSCREEN_ENABLE_SPACY', '').strip().lower() in {'1', 'true', 'yes', 'on'}


class ResumeParser:
    def __init__(self, nlp_model: str = 'en_core_web_sm'):
        self._nlp = None               # Lazy-loaded spaCy model; only loaded when ENABLE_SPACY=1
        self._nlp_load_failed = False  # Flag to avoid retrying a failed load on every call
        self._nlp_model = nlp_model    # Change the NER model by passing a different name here
        self.common_skills = self._load_common_skills()  # Modify _load_common_skills() to add/remove skill keywords

    def parse(self, file_obj, filename: Optional[str] = None) -> ResumeProfile:
        raw_text = self.extract_text(file_obj, filename=filename)
        profile = ResumeProfile(raw_text=raw_text)
        profile.email = self.extract_email(raw_text)
        profile.phone = self.extract_phone(raw_text)
        profile.skills = self.extract_skills(raw_text)
        profile.education = normalize_education_entries(self.extract_education(raw_text))
        profile.experience = normalize_list_text(self.extract_experience(raw_text))
        profile.organizations, profile.locations, profile.full_name = self.extract_entities(raw_text)
        return profile

    def extract_text(self, file_obj, filename: Optional[str] = None) -> str:
        ext = self._get_extension(file_obj, filename)
        if ext == '.pdf':
            return self._extract_text_from_pdf(file_obj)
        if ext == '.docx':
            return self._extract_text_from_docx(file_obj)
        raise ValueError('Unsupported resume format. Please upload a PDF or DOCX file.')

    def _extract_text_from_pdf(self, file_obj) -> str:
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)

        text = ''
        # Primary extraction: pdfplumber handles multi-column and complex layouts better
        if pdfplumber is not None:
            try:
                with pdfplumber.open(file_obj) as pdf:
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text + '\n'
            except Exception:
                logger.exception('pdfplumber failed to extract resume text.')
                text = ''

        # Fallback to PyPDF2 if pdfplumber returned less than 100 characters
        # (e.g. scanned/image-based PDF where pdfplumber finds no text)
        if len(text.strip()) < 100:
            if PdfReader is None:
                raise ValueError('PDF support is not installed. Add PyPDF2 to parse resume uploads.')
            if hasattr(file_obj, 'seek'):
                file_obj.seek(0)  # Rewind so PyPDF2 reads from the beginning
            try:
                reader = PdfReader(file_obj)
                pages = []
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        pages.append(page_text)
                text = '\n'.join(pages)
            except Exception as exc:
                logger.exception('PyPDF2 failed to extract resume text.')
                raise ValueError(f'Could not read the uploaded PDF: {exc}') from exc

        return text.strip()

    def _extract_text_from_docx(self, file_obj) -> str:
        if Document is None:
            raise ValueError('DOCX support is not installed. Add python-docx to parse resume uploads.')
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
        try:
            document = Document(file_obj)
        except Exception as exc:
            logger.exception('python-docx failed to extract resume text.')
            raise ValueError(f'Could not read the uploaded DOCX: {exc}') from exc

        lines = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        lines.append(cell.text.strip())
        return '\n'.join(lines)

    def extract_email(self, text: str) -> Optional[str]:
        matches = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', text, re.IGNORECASE)
        return matches[0] if matches else None

    def extract_phone(self, text: str) -> Optional[str]:
        # Two patterns: international format (+63 ...) and North American (XXX-XXX-XXXX)
        # Add a new pattern to the list if phone numbers in your locale aren't being detected
        patterns = [
            r'\+?\d[\d\s().-]{8,}\d',
            r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                digits = re.sub(r'\D', '', match)  # Strip non-digits to count actual digit length
                if len(digits) >= 10:              # Require at least 10 digits to avoid false positives
                    return match.strip()
        return None

    def extract_skills(self, text: str) -> list[str]:
        return extract_canonical_skills(text)

    def extract_education(self, text: str) -> list[str]:
        # Add or remove keywords here to control which lines are captured as education entries
        keywords = [
            'university', 'college', 'institute', 'school', 'academy',
            'bachelor', 'master', 'phd', 'mba', 'degree', 'diploma', 'certification',
        ]
        section_text = self._extract_first_section_text(
            text,
            ['education', 'academic background', 'academic qualifications', 'qualifications'],
            ['experience', 'work experience', 'employment history', 'professional experience', 'projects', 'skills', 'certifications'],
        )
        if section_text:
            lines = self._extract_section_lines(section_text, keywords, min_length=10, max_length=200, limit=5)
            if lines:
                return lines
        # limit=5: returns up to 5 lines; increase if education sections are being cut off
        return self._extract_keyword_lines(text, keywords, min_length=10, max_length=200, limit=5)

    def extract_experience(self, text: str) -> list[str]:
        # Add or remove keywords here to control which lines are captured as experience entries
        keywords = [
            'experience', 'worked', 'employed', 'position', 'role', 'job', 'responsibilities',
            'achievements', 'developer', 'engineer', 'analyst', 'manager', 'lead', 'specialist',
        ]
        section_text = self._extract_first_section_text(
            text,
            ['experience', 'work experience', 'employment history', 'professional experience', 'career history', 'projects'],
            ['education', 'academic background', 'skills', 'certifications', 'references'],
        )
        if section_text:
            lines = self._extract_section_lines(section_text, keywords + ['years', 'yrs'], min_length=20, max_length=220, limit=8)
            if lines:
                return lines
        # limit=8: returns up to 8 lines; increase if experience sections are being cut off
        return self._extract_keyword_lines(text, keywords, min_length=20, max_length=220, limit=8)

    def extract_entities(self, text: str) -> tuple[list[str], list[str], Optional[str]]:
        organizations = []
        locations = []
        full_name = None
        nlp = self._get_nlp()
        if nlp is not None and text.strip():
            # Cap input to keep NER fast; name is always near the top,
            # organisations and locations are collected throughout the cap.
            doc = nlp(text[:6000])
            persons = []
            for entity in doc.ents:
                entity_text = entity.text.strip()
                if not entity_text:
                    continue
                if entity.label_ == 'ORG':
                    organizations.append(entity_text)
                elif entity.label_ in {'GPE', 'LOC'}:
                    locations.append(entity_text)
                elif entity.label_ == 'PERSON':
                    persons.append(entity_text)
            # doc.ents is ordered by position, so the first valid PERSON
            # is the one closest to the top of the resume — the applicant.
            full_name = next((person for person in persons if 1 < len(person.split()) < 5), None)

        if not full_name:
            full_name = self._guess_name(text)

        normalized_organizations = normalize_list_text(organizations)
        normalized_locations = normalize_list_text(normalize_location_text(location) for location in locations)
        return normalized_organizations, normalized_locations, full_name

    def _get_nlp(self):
        if self._nlp is not None:
            return self._nlp
        if self._nlp_load_failed or spacy is None or not ENABLE_SPACY:
            return None
        try:
            self._nlp = spacy.load(self._nlp_model)
        except OSError:
            logger.info('spaCy model %s is not installed; using regex-based resume parsing.', self._nlp_model)
            self._nlp_load_failed = True
            return None
        return self._nlp

    def _guess_name(self, text: str) -> Optional[str]:
        # Scans the first 8 lines of the resume for a plausible full name.
        # A candidate line must: have 2-4 words, start with a capital letter, contain no '@'.
        # Increase the [:8] range if applicant names are usually further down in the file.
        excluded_tokens = {'resume', 'curriculum vitae', 'cv', 'experience', 'education', 'skills', 'profile', 'summary'}
        for line in text.splitlines()[:8]:
            candidate = ' '.join(line.split()).strip()
            normalized_candidate = candidate.lower().rstrip(':')
            if not candidate or '@' in candidate or len(candidate) > 60 or any(char.isdigit() for char in candidate):
                continue
            if normalized_candidate in excluded_tokens:
                continue
            words = candidate.split()
            if 1 < len(words) < 5 and all(word[:1].isupper() for word in words if word.isalpha()):
                return candidate
        return None

    def _extract_first_section_text(self, text: str, section_names: list[str], stop_names: list[str]) -> str:
        lines = text.splitlines()
        start_index = None
        for index, line in enumerate(lines):
            normalized = ' '.join(line.strip().lower().rstrip(':').split())
            if normalized in section_names:
                start_index = index + 1
                break

        if start_index is None:
            return ''

        collected = []
        for line in lines[start_index:]:
            stripped = ' '.join(line.split()).strip()
            if not stripped:
                if collected:
                    collected.append('')
                continue

            normalized = stripped.lower().rstrip(':')
            if normalized in stop_names:
                break
            collected.append(stripped)

        return '\n'.join(collected).strip()

    def _extract_section_lines(
        self,
        text: str,
        keywords: list[str],
        min_length: int,
        max_length: int,
        limit: int,
    ) -> list[str]:
        lines = []
        for raw_line in text.splitlines():
            line = ' '.join(raw_line.split())
            line_lower = line.lower()
            line = re.sub(r'^[•\-*\d.)\s]+', '', line).strip()
            if any(keyword in line_lower for keyword in keywords) and min_length < len(line) < max_length:
                lines.append(line)
        return lines[:limit]

    def _extract_keyword_lines(
        self,
        text: str,
        keywords: list[str],
        min_length: int,
        max_length: int,
        limit: int,
    ) -> list[str]:
        lines = []
        for raw_line in text.splitlines():
            line = ' '.join(raw_line.split())
            line_lower = line.lower()
            if any(keyword in line_lower for keyword in keywords) and min_length < len(line) < max_length:
                lines.append(line)
        return lines[:limit]

    def _get_extension(self, file_obj: IO, filename: Optional[str]) -> str:
        name = filename or getattr(file_obj, 'name', '')
        return os.path.splitext(str(name))[1].lower()

    def _load_common_skills(self) -> list[str]:
        return get_canonical_skills()