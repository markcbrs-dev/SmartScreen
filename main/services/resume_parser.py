import importlib
import logging
import os
import re
from typing import IO, Optional

from .entities import ResumeProfile

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
        self._nlp = None
        self._nlp_load_failed = False
        self._nlp_model = nlp_model
        self.common_skills = self._load_common_skills()

    def parse(self, file_obj, filename: Optional[str] = None) -> ResumeProfile:
        raw_text = self.extract_text(file_obj, filename=filename)
        profile = ResumeProfile(raw_text=raw_text)
        profile.email = self.extract_email(raw_text)
        profile.phone = self.extract_phone(raw_text)
        profile.skills = self.extract_skills(raw_text)
        profile.education = self.extract_education(raw_text)
        profile.experience = self.extract_experience(raw_text)
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

        if len(text.strip()) < 100:
            if PdfReader is None:
                raise ValueError('PDF support is not installed. Add PyPDF2 to parse resume uploads.')
            if hasattr(file_obj, 'seek'):
                file_obj.seek(0)
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
        patterns = [
            r'\+?\d[\d\s().-]{8,}\d',
            r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                digits = re.sub(r'\D', '', match)
                if len(digits) >= 10:
                    return match.strip()
        return None

    def extract_skills(self, text: str) -> list[str]:
        text_lower = text.lower()
        skills = []
        for skill in self.common_skills:
            pattern = r'\b' + re.escape(skill) + r'\b'
            if re.search(pattern, text_lower):
                skills.append(skill.title())
        return sorted(set(skills))

    def extract_education(self, text: str) -> list[str]:
        keywords = [
            'university', 'college', 'institute', 'school', 'academy',
            'bachelor', 'master', 'phd', 'mba', 'degree', 'diploma', 'certification',
        ]
        return self._extract_keyword_lines(text, keywords, min_length=10, max_length=200, limit=5)

    def extract_experience(self, text: str) -> list[str]:
        keywords = [
            'experience', 'worked', 'employed', 'position', 'role', 'job', 'responsibilities',
            'achievements', 'developer', 'engineer', 'analyst', 'manager', 'lead', 'specialist',
        ]
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

        return sorted(set(organizations)), sorted(set(locations)), full_name

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
        for line in text.splitlines()[:8]:
            candidate = ' '.join(line.split()).strip()
            if not candidate or '@' in candidate or len(candidate) > 60:
                continue
            words = candidate.split()
            if 1 < len(words) < 5 and all(word[:1].isupper() for word in words if word.isalpha()):
                return candidate
        return None

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
        # Keep in sync with _SKILL_VOCAB in matcher.py and tech_skills in job_parser.py.
        return [
            # Languages
            'python', 'java', 'javascript', 'typescript', 'c++', 'c#', 'ruby', 'go', 'rust',
            'kotlin', 'swift', 'php',
            # Web / frontend
            'html', 'css', 'react', 'angular', 'vue', 'node.js', 'express', 'flutter',
            # Backend / frameworks
            'django', 'flask', 'fastapi', 'graphql', 'rest api', 'microservices',
            # Databases
            'sql', 'mongodb', 'postgresql', 'mysql', 'redis', 'aws', 'azure', 'gcp',
            # Cloud & DevOps
            'docker', 'kubernetes', 'jenkins', 'terraform', 'git', 'linux',
            # Data / AI
            'machine learning', 'deep learning', 'ai', 'data science', 'data analysis',
            'tableau', 'power bi', 'excel', 'pandas', 'numpy',
            # Methodologies
            'agile', 'scrum', 'devops',
            # Soft skills — keep in sync with _load_soft_skills in job_parser.py
            'communication', 'teamwork', 'leadership', 'problem solving', 'critical thinking',
            'time management', 'adaptability', 'creativity', 'collaboration', 'decision making',
        ]