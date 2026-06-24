"""
main/services/resume_parser.py — Extracts structured data from uploaded resume files.

OVERVIEW
--------
ResumeParser.parse() is the main entry point.
It accepts a file-like object (PDF or DOCX) and returns a ResumeProfile dataclass.

EXTRACTION PIPELINE
-------------------
    1. extract_text()      — reads raw text from PDF (PyMuPDF → pdfplumber → PyPDF2 fallback) or DOCX
  2. extract_email()     — regex: finds first valid email address
  3. extract_phone()     — regex: finds first valid phone number
  4. extract_skills()    — keyword match against common_skills vocabulary
  5. extract_education() — keyword-line heuristic (looks for university, degree, etc.)
  6. extract_experience()— keyword-line heuristic (looks for engineer, manager, etc.)
  7. extract_entities()  — spaCy NER (ORG, GPE, PERSON) if SMARTSCREEN_ENABLE_SPACY=1
                           Falls back to _guess_name() from the first 8 lines.
    8. _parse_with_gemini()— optional Gemini enrichment if SMARTSCREEN_ENABLE_GEMINI=1
                                                     and core fields are still missing after local parsing.

HOW TO MODIFY
-------------
  Add a new skill         → add it to _load_common_skills() list
                            Keep in sync with _SKILL_VOCAB in matcher.py
  Change education keywords → edit the `keywords` list in extract_education()
  Change experience keywords→ edit the `keywords` list in extract_experience()
  Enable spaCy NER        → set env var SMARTSCREEN_ENABLE_SPACY=1 and install en_core_web_sm
    Enable Gemini fallback  → set SMARTSCREEN_ENABLE_GEMINI=1 and GEMINI_API_KEY
  Change NLP model        → pass nlp_model='...' to ResumeParser() in recruitment.py
    Change PDF extraction   → edit _extract_text_from_pdf() (primary: PyMuPDF, fallbacks: pdfplumber, PyPDF2)
  Change DOCX extraction  → edit _extract_text_from_docx()
  Increase name-guessing  → edit _guess_name() (currently scans first 8 lines)

DEBUG
-----
    No text extracted?    → check that PyMuPDF, pdfplumber, or PyPDF2 are installed; check resume file format
  Skills always empty?  → check _load_common_skills() and that resume has recognisable keywords
  Name always None?     → check _guess_name(); resume may not have the name on a clean first line
  spaCy not running?    → check SMARTSCREEN_ENABLE_SPACY env var and en_core_web_sm installation
"""

import importlib
import json
import logging
import os
import re
import tempfile
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
    import fitz
except ImportError:
    fitz = None

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
    from google import genai
except ImportError:
    genai = None

try:
    spacy = importlib.import_module('spacy')
except ImportError:
    spacy = None

try:
    from resume_parser_pro import ResumeParser as ResumeParserPro
except ImportError:
    ResumeParserPro = None


ENABLE_SPACY = os.environ.get('SMARTSCREEN_ENABLE_SPACY', '').strip().lower() in {'1', 'true', 'yes', 'on'}
ENABLE_GEMINI = os.environ.get('SMARTSCREEN_ENABLE_GEMINI', '').strip().lower() in {'1', 'true', 'yes', 'on'}
DEFAULT_GEMINI_MODEL = os.environ.get('SMARTSCREEN_GEMINI_MODEL', 'gemini-2.5-flash').strip() or 'gemini-2.5-flash'


class ResumeParser:
    # Words that commonly follow a technology/skill name but are never part of a person's name.
    # Extend this set if new tech-descriptor false-positives are reported.
    _TECH_DESCRIPTOR_WORDS: frozenset[str] = frozenset({
        'framework', 'library', 'runtime', 'engine', 'platform', 'sdk',
        'language', 'tool', 'module', 'plugin', 'software', 'technology',
        'technologies', 'scripting', 'stack', 'interface', 'database',
        'server', 'service', 'cloud',
    })
    _SURNAME_PREFIXES: frozenset[str] = frozenset({
        'da', 'de', 'del', 'dela', 'de la', 'de los', 'dela', 'di', 'dos', 'du',
        'la', 'las', 'los', 'mac', 'mc', 'san', 'santa', 'st', 'st.', 'van', 'vander', 'von',
    })

    def __init__(self, nlp_model: str = 'en_core_web_sm', gemini_model: Optional[str] = None):
        self._nlp = None               # Lazy-loaded spaCy model; only loaded when ENABLE_SPACY=1
        self._nlp_load_failed = False  # Flag to avoid retrying a failed load on every call
        self._nlp_model = nlp_model    # Change the NER model by passing a different name here
        self._gemini_client = None
        self._gemini_load_failed = False
        self._gemini_model = (gemini_model or DEFAULT_GEMINI_MODEL).strip() or 'gemini-2.5-flash'
        self.common_skills = self._load_common_skills()  # Modify _load_common_skills() to add/remove skill keywords
        # Lowercase set of all canonical skill names used to reject skill phrases
        # that appear at the top of a resume and could be misread as a person's name.
        self._skill_name_set: frozenset[str] = frozenset(s.lower() for s in self.common_skills)

    def parse(self, file_obj, filename: Optional[str] = None) -> ResumeProfile:
        raw_text = self.extract_text(file_obj, filename=filename)
        profile = ResumeProfile(raw_text=raw_text)
        profile.email = self.extract_email(raw_text)
        profile.phone = self.extract_phone(raw_text)
        profile.skills = self.extract_skills(raw_text)
        profile.education = normalize_education_entries(self.extract_education(raw_text))
        profile.experience = normalize_list_text(self.extract_experience(raw_text))
        profile.organizations, profile.locations, fallback_name = self.extract_entities(raw_text)
        pro_data = {}
        if self._should_use_resume_parser_pro(fallback_name, profile.email, profile.phone):
            pro_data = self._parse_with_resume_parser_pro(file_obj, filename=filename)
            if not profile.email:
                profile.email = self._first_resume_parser_pro_value(pro_data.get('email'))
            if not profile.phone:
                profile.phone = self._normalize_phone_number(
                    self._first_resume_parser_pro_value(pro_data.get('phone_number'))
                )
        gemini_data = {}
        if self._should_use_gemini_name_extraction(raw_text):
            gemini_data = self._parse_with_gemini(raw_text, filename=filename)
        profile.full_name = self._select_best_full_name(
            self._first_resume_parser_pro_value(gemini_data.get('full_name')),
            self._select_best_full_name(
                self._first_resume_parser_pro_value(pro_data.get('name')),
                fallback_name,
            ),
        )
        filename_name = self._extract_name_from_filename(filename or getattr(file_obj, 'name', None))
        if not raw_text.strip() and filename_name:
            if not profile.full_name or not self._names_share_tokens(profile.full_name, filename_name):
                profile.full_name = filename_name
        if not profile.full_name:
            profile.full_name = filename_name
        if gemini_data:
            self._merge_gemini_profile(profile, gemini_data)
        elif self._should_use_gemini(profile):
            self._merge_gemini_profile(
                profile,
                self._parse_with_gemini(raw_text, filename=filename),
            )
        return profile

    def _should_use_resume_parser_pro(
        self,
        fallback_name: Optional[str],
        email: Optional[str],
        phone: Optional[str],
    ) -> bool:
        if ResumeParserPro is None:
            return False
        if not email or not phone:
            return True
        return self._count_name_parts(fallback_name or '') < 2

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
        # Primary extraction: PyMuPDF is faster and preserves more text on the
        # resume PDFs we see locally, especially when layout extraction is sparse.
        if fitz is not None:
            try:
                with fitz.open(stream=file_obj.read(), filetype='pdf') as pdf:
                    for page in pdf:
                        page_text = page.get_text()
                        if page_text:
                            text += page_text + '\n'
            except Exception:
                logger.exception('PyMuPDF failed to extract resume text.')
                text = ''

        # Fallback to pdfplumber if PyMuPDF returned very little text.
        if len(text.strip()) < 100 and pdfplumber is not None:
            if hasattr(file_obj, 'seek'):
                file_obj.seek(0)
            try:
                with pdfplumber.open(file_obj) as pdf:
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text + '\n'
            except Exception:
                logger.exception('pdfplumber failed to extract resume text.')
                text = ''

        # Final fallback to PyPDF2 if richer extractors returned very little text.
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

    def _parse_with_resume_parser_pro(self, file_obj, filename: Optional[str] = None) -> dict[str, object]:
        if ResumeParserPro is None:
            return {}

        ext = self._get_extension(file_obj, filename)
        if ext not in {'.pdf', '.docx'}:
            return {}

        temp_path = None
        try:
            temp_path = self._write_temp_resume_file(file_obj, ext)
            if not temp_path:
                return {}

            parser = ResumeParserPro(temp_path)
            parsed = parser.parse()
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            logger.exception('resume-parser-pro failed to parse resume. Falling back to built-in heuristics.')
            return {}
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    logger.debug('Could not remove temporary resume-parser-pro file: %s', temp_path)
            if hasattr(file_obj, 'seek'):
                file_obj.seek(0)

    def _should_use_gemini(self, profile: ResumeProfile) -> bool:
        if not profile.raw_text.strip():
            return False
        if self._get_gemini_client() is None:
            return False
        return any((
            not profile.full_name,
            not profile.email,
            not profile.phone,
            not profile.skills,
            not profile.education,
            not profile.experience,
        ))

    def _should_use_gemini_name_extraction(self, text: str) -> bool:
        if not text.strip():
            return False
        return self._get_gemini_client() is not None

    def _get_gemini_client(self):
        if self._gemini_client is not None:
            return self._gemini_client
        if self._gemini_load_failed or genai is None or not ENABLE_GEMINI:
            return None

        api_key = os.environ.get('GEMINI_API_KEY', '').strip()
        if not api_key:
            logger.info('Gemini enrichment is enabled but GEMINI_API_KEY is not set; skipping Gemini parsing.')
            self._gemini_load_failed = True
            return None

        try:
            self._gemini_client = genai.Client(api_key=api_key)
        except Exception:
            logger.exception('Could not initialize the Gemini client; skipping Gemini parsing.')
            self._gemini_load_failed = True
            return None
        return self._gemini_client

    def _parse_with_gemini(self, text: str, filename: Optional[str] = None) -> dict[str, object]:
        client = self._get_gemini_client()
        if client is None:
            return {}

        try:
            response = client.models.generate_content(
                model=self._gemini_model,
                contents=self._build_gemini_prompt(text, filename=filename),
            )
        except Exception:
            logger.exception('Gemini failed to enrich the resume profile.')
            return {}

        return self._parse_gemini_response(getattr(response, 'text', '') or '')

    def _build_gemini_prompt(self, text: str, filename: Optional[str] = None) -> str:
        prompt_lines = [
            'Extract structured resume data from the resume text below.',
            'Return JSON only. Do not include markdown or commentary.',
            'Use this exact schema:',
            '{',
            '  "full_name": string | null,',
            '  "email": string | null,',
            '  "phone": string | null,',
            '  "skills": string[],',
            '  "education": string[],',
            '  "experience": string[],',
            '  "organizations": string[],',
            '  "locations": string[]',
            '}',
            'Rules:',
            '- Use null for missing scalar values and [] for missing arrays.',
            '- full_name must be the applicant name, not a role title, degree, or skill phrase.',
            '- Keep skills concise.',
            '- Keep education and experience as short resume line items.',
            '- Extract only information present in the text.',
        ]
        if filename:
            prompt_lines.append(f'Filename: {filename}')
        prompt_lines.extend([
            'Resume text:',
            text[:20000],
        ])
        return '\n'.join(prompt_lines)

    def _parse_gemini_response(self, response_text: str) -> dict[str, object]:
        if not response_text.strip():
            return {}

        candidate = response_text.strip()
        if candidate.startswith('```'):
            candidate = re.sub(r'^```(?:json)?\s*', '', candidate, flags=re.IGNORECASE)
            candidate = re.sub(r'\s*```$', '', candidate)

        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', candidate, re.DOTALL)
            if not match:
                logger.warning('Gemini response did not contain valid JSON.')
                return {}
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                logger.warning('Gemini response JSON could not be decoded.')
                return {}

        return parsed if isinstance(parsed, dict) else {}

    def _merge_gemini_profile(self, profile: ResumeProfile, gemini_data: dict[str, object]) -> None:
        if not gemini_data:
            return

        profile.full_name = self._select_best_full_name(
            self._first_resume_parser_pro_value(gemini_data.get('full_name')),
            profile.full_name,
        )

        if not profile.email:
            email_candidate = self._first_resume_parser_pro_value(gemini_data.get('email'))
            profile.email = self.extract_email(email_candidate or '') or email_candidate

        if not profile.phone:
            phone_candidate = self._first_resume_parser_pro_value(gemini_data.get('phone'))
            extracted_phone = self.extract_phone(phone_candidate or '')
            profile.phone = extracted_phone or self._normalize_phone_number(phone_candidate)

        profile.skills = self._merge_unique_items(
            profile.skills,
            self._extract_skills_from_gemini(gemini_data.get('skills')),
        )
        profile.education = normalize_education_entries(
            self._merge_unique_items(
                profile.education,
                self._coerce_text_list(gemini_data.get('education')),
            )
        )
        profile.experience = normalize_list_text(
            self._merge_unique_items(
                profile.experience,
                self._coerce_text_list(gemini_data.get('experience')),
            )
        )
        profile.organizations = normalize_list_text(
            self._merge_unique_items(
                profile.organizations,
                self._coerce_text_list(gemini_data.get('organizations')),
            )
        )
        profile.locations = normalize_list_text(
            normalize_location_text(location)
            for location in self._merge_unique_items(
                profile.locations,
                self._coerce_text_list(gemini_data.get('locations')),
            )
        )

    def _write_temp_resume_file(self, file_obj, extension: str) -> Optional[str]:
        if not hasattr(file_obj, 'read'):
            return None

        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)

        data = file_obj.read()
        if not data:
            return None
        if isinstance(data, str):
            data = data.encode('utf-8')

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=extension)
        try:
            temp_file.write(data)
            return temp_file.name
        finally:
            temp_file.close()

    def _first_resume_parser_pro_value(self, value: object) -> Optional[str]:
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None

        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return item.strip()

        return None

    def _coerce_text_list(self, value: object, split_pattern: str = r'[\r\n]+') -> list[str]:
        items = []
        if isinstance(value, str):
            items = re.split(split_pattern, value)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                if isinstance(item, str):
                    items.extend(re.split(split_pattern, item))
                elif item is not None:
                    items.append(str(item))

        return normalize_list_text(item.strip() for item in items if str(item).strip())

    def _extract_skills_from_gemini(self, value: object) -> list[str]:
        text = '\n'.join(self._coerce_text_list(value, split_pattern=r'[\r\n,;]+'))
        if not text:
            return []
        return self._filter_ambiguous_full_text_skills(
            text,
            self._filter_education_domain_skills(text, extract_canonical_skills(text)),
        )

    def _merge_unique_items(self, existing_items: list[str], new_items: list[str]) -> list[str]:
        merged = []
        seen = set()
        for item in [*existing_items, *new_items]:
            cleaned = ' '.join(str(item).split()).strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(cleaned)
        return merged

    def _looks_like_skill_not_name(self, candidate: str) -> bool:
        """Return True when *candidate* is almost certainly a skill name or tech phrase,
        not a person's full name.  Two checks are applied:

        1. Exact match against the canonical skill vocabulary (e.g. 'Machine Learning',
           'Computer Science', 'Information Technology').
        2. Any word in the candidate belongs to the tech-descriptor set (e.g. 'framework',
           'library', 'platform') — catches phrases like 'Django Framework'.
        """
        normalized = candidate.lower().strip()
        if normalized in self._skill_name_set:
            return True
        return bool(set(normalized.split()) & self._TECH_DESCRIPTOR_WORDS)

    def _normalize_full_name(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None

        normalized = self._normalize_name_candidate(value)
        if not normalized:
            return None

        words = []
        for part in normalized.split():
            if not part:
                continue
            stripped = re.sub(r"[^A-Za-z]", '', part)
            if stripped and len(stripped) <= 3 and stripped.upper() in {'II', 'III', 'IV', 'VI', 'VII', 'VIII', 'IX', 'JR', 'SR'}:
                words.append(stripped.upper())
                continue
            words.append(part.title())

        formatted = ' '.join(words).strip()
        if not formatted:
            return None
        # Final safety net: reject names that look like skill names or tech phrases
        # (catches cases where resume_parser_pro itself returns a skill string as the name).
        if self._looks_like_skill_not_name(formatted):
            return None
        return formatted

    def _select_best_full_name(self, primary_value: Optional[str], fallback_value: Optional[str]) -> Optional[str]:
        primary_name = self._normalize_full_name(primary_value)
        fallback_name = self._normalize_full_name(fallback_value)

        if not primary_name:
            return fallback_name
        if not fallback_name:
            return primary_name

        primary_parts = self._count_name_parts(primary_name)
        fallback_parts = self._count_name_parts(fallback_name)

        if primary_parts < 2 <= fallback_parts:
            return fallback_name
        if fallback_parts > primary_parts and self._is_name_extension(primary_name, fallback_name):
            return fallback_name
        return primary_name

    def split_name_components(self, full_name: Optional[str]) -> tuple[str, str, str]:
        normalized_name = self._normalize_full_name(full_name)
        if not normalized_name:
            return '', '', ''

        parts = normalized_name.split()
        if len(parts) == 1:
            return parts[0], '', ''
        if len(parts) == 2:
            return parts[0], '', parts[1]

        middle_initial_index = next((index for index, part in enumerate(parts[1:], start=1) if self._is_middle_initial(part)), None)
        if middle_initial_index is not None:
            middle_end_index = middle_initial_index
            while middle_end_index < len(parts) and self._is_middle_initial(parts[middle_end_index]):
                middle_end_index += 1

            first_name = ' '.join(parts[:middle_initial_index]).strip()
            middle_name = ' '.join(parts[middle_initial_index:middle_end_index]).strip()
            last_name = ' '.join(parts[middle_end_index:]).strip()
            return first_name, middle_name, last_name

        last_name_start = self._find_last_name_start_index(parts)
        first_name = ' '.join(parts[:last_name_start]).strip()
        last_name = ' '.join(parts[last_name_start:]).strip()
        return first_name, '', last_name

    def _find_last_name_start_index(self, parts: list[str]) -> int:
        if len(parts) <= 2:
            return max(len(parts) - 1, 0)

        start_index = len(parts) - 1
        while start_index - 1 >= 1:
            candidate = re.sub(r'[^A-Za-z]', '', parts[start_index - 1]).lower()
            if candidate not in self._SURNAME_PREFIXES:
                break
            start_index -= 1
        return start_index

    def _is_middle_initial(self, value: str) -> bool:
        compact = re.sub(r'[^A-Za-z.]', '', value or '')
        return bool(re.fullmatch(r'[A-Za-z]\.?', compact))

    def _count_name_parts(self, value: str) -> int:
        return len([part for part in value.split() if re.search(r'[A-Za-z]', part)])

    def _names_share_tokens(self, left_name: str, right_name: str) -> bool:
        left_tokens = {
            token.lower()
            for token in re.findall(r'[A-Za-z]+', left_name)
            if len(token) > 1
        }
        right_tokens = {
            token.lower()
            for token in re.findall(r'[A-Za-z]+', right_name)
            if len(token) > 1
        }
        if not left_tokens or not right_tokens:
            return False
        return bool(left_tokens & right_tokens)

    def _is_name_extension(self, shorter_name: str, longer_name: str) -> bool:
        shorter_parts = [part for part in shorter_name.lower().split() if part]
        longer_parts = [part for part in longer_name.lower().split() if part]
        if not shorter_parts or len(longer_parts) <= len(shorter_parts):
            return False

        shorter_index = 0
        for part in longer_parts:
            if shorter_index < len(shorter_parts) and part == shorter_parts[shorter_index]:
                shorter_index += 1
        return shorter_index == len(shorter_parts)

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
                    normalized = self._normalize_phone_number(match)
                    return normalized or match.strip()
        return None

    def _normalize_phone_number(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None

        digits = re.sub(r'\D', '', value)
        local_number = None

        if len(digits) == 11 and digits.startswith('09'):
            local_number = digits[1:]
        elif len(digits) == 12 and digits.startswith('639'):
            local_number = digits[2:]
        elif len(digits) == 10 and digits.startswith('9'):
            local_number = digits

        if not local_number or len(local_number) != 10:
            return value.strip() if isinstance(value, str) else None

        return f'+63 {local_number[:3]} {local_number[3:6]} {local_number[6:]}'

    def extract_skills(self, text: str) -> list[str]:
        skills_section_text = self._extract_first_section_text(
            text,
            ['skills', 'technical skills', 'core competencies', 'competencies', 'expertise', 'tech stack'],
            ['experience', 'work experience', 'employment history', 'professional experience', 'education', 'projects', 'certifications', 'references'],
        )
        if skills_section_text:
            return self._filter_education_domain_skills(
                skills_section_text,
                extract_canonical_skills(skills_section_text),
            )

        return self._filter_ambiguous_full_text_skills(
            text,
            self._filter_education_domain_skills(text, extract_canonical_skills(text)),
        )

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
            leading_context_lines=6,
        )
        if section_text:
            lines = self._extract_ranked_education_lines(section_text, limit=5)
            if lines:
                return lines
        lines = self._extract_ranked_education_lines(text, limit=5)
        if lines:
            return lines
        # limit=5: returns up to 5 lines; increase if education sections are being cut off
        return self._extract_keyword_lines(text, keywords, min_length=10, max_length=200, limit=5)

    def extract_experience(self, text: str) -> list[str]:
        # Add or remove keywords here to control which lines are captured as experience entries
        keywords = [
            'experience', 'worked', 'employed', 'position', 'role', 'job', 'responsibilities',
            'achievements', 'developer', 'engineer', 'analyst', 'manager', 'lead', 'specialist',
            'assistant', 'associate', 'attendant', 'barista', 'bartender', 'bellman', 'busser', 'bussing', 'butler', 'cashier', 'clerk',
            'coordinator', 'customer service', 'front desk', 'host', 'housekeeping', 'officer',
            'operations', 'receptionist', 'recruiter', 'representative', 'sales', 'scheduler',
            'secretary', 'server', 'staff', 'supervisor', 'teacher', 'trainer', 'helper', 'kitchen', 'fitness', 'support',
        ]
        section_text = self._extract_first_section_text(
            text,
            ['experience', 'work experience', 'employment history', 'professional experience', 'career history', 'projects'],
            ['education', 'academic background', 'skills', 'certifications', 'references'],
            leading_context_lines=6,
        )
        title_lines = []
        if section_text:
            title_lines.extend(self._extract_ranked_experience_title_lines(section_text, limit=8))
        title_lines.extend(self._extract_ranked_experience_title_lines(text, limit=8))
        title_lines = self._dedupe_lines(title_lines)
        if title_lines:
            return title_lines[:8]

        if section_text:
            lines = self._extract_section_lines(section_text, keywords + ['years', 'yrs'], min_length=20, max_length=220, limit=8)
            if lines:
                return lines
        # limit=5: returns up to 5 lines; increase if education sections are being cut off
        # limit=8: returns up to 8 lines; increase if experience sections are being cut off
        return self._extract_keyword_lines(text, keywords, min_length=20, max_length=220, limit=8)

    def extract_entities(self, text: str) -> tuple[list[str], list[str], Optional[str]]:
        organizations = []
        locations = []
        full_name = None
        guessed_name = None
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

        guessed_name = self._guess_name(text)
        full_name = self._select_best_full_name(full_name, guessed_name)

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
        # Scans the first 12 lines of the resume for a plausible full name.
        # A candidate line must: have 2-4 words, start with a capital letter, contain no '@'.
        # Increase the [:12] range if applicant names are usually further down in the file.
        raw_lines = [line for line in text.splitlines()[:12] if line.strip()]
        candidates = []
        for index, line in enumerate(raw_lines):
            candidates.append(line)
            if index + 1 < len(raw_lines):
                candidates.append(f'{line} {raw_lines[index + 1]}')
            if index + 2 < len(raw_lines):
                candidates.append(f'{line} {raw_lines[index + 1]} {raw_lines[index + 2]}')

        for candidate_text in candidates:
            candidate = self._normalize_name_candidate(candidate_text)
            if self._is_plausible_name_candidate(candidate):
                return candidate

        isolated_name = self._guess_name_from_isolated_lines(text)
        if isolated_name:
            return isolated_name
        return None

    def _guess_name_from_isolated_lines(self, text: str) -> Optional[str]:
        raw_lines = [line.strip() for line in text.splitlines()[:24] if line.strip()]
        single_word_candidates = []

        for line in raw_lines:
            normalized = self._normalize_name_candidate(line)
            if ' ' in normalized or '@' in normalized or any(char.isdigit() for char in normalized):
                continue
            letters_only = re.sub(r'[^A-Za-z.-]', '', normalized)
            if len(letters_only) < 2:
                continue
            if not any(char.isalpha() for char in normalized):
                continue
            if normalized.upper() != normalized and normalized.title() != normalized:
                continue
            if self._looks_like_skill_not_name(normalized):
                continue
            single_word_candidates.append(normalized)

        for start in range(len(single_word_candidates)):
            for length in range(2, min(4, len(single_word_candidates) - start) + 1):
                candidate = ' '.join(single_word_candidates[start:start + length])
                if self._is_plausible_name_candidate(candidate):
                    return candidate

        return None

    def _normalize_name_candidate(self, line: str) -> str:
        candidate = ' '.join(line.split()).strip()
        candidate = re.sub(r'^(?:full\s+name|name)\s*:\s*', '', candidate, flags=re.IGNORECASE)
        if ',' in candidate:
            parts = [part.strip() for part in candidate.split(',') if part.strip()]
            if len(parts) == 2:
                candidate = f'{parts[1]} {parts[0]}'
        return candidate.strip(' -:\t')

    def _extract_name_from_filename(self, filename: Optional[str]) -> Optional[str]:
        if not filename:
            return None

        stem = os.path.splitext(os.path.basename(filename))[0]
        candidate = re.sub(r'[_-]+', ' ', stem)
        candidate = re.sub(r'\b(?:resume|cv|curriculum vitae)\b', '', candidate, flags=re.IGNORECASE)
        candidate = self._normalize_name_candidate(candidate)
        if not self._is_plausible_name_candidate(candidate):
            return None
        return self._normalize_full_name(candidate)

    def _is_plausible_name_candidate(self, candidate: str) -> bool:
        excluded_tokens = {'resume', 'curriculum vitae', 'cv', 'experience', 'education', 'skills', 'profile', 'summary'}
        excluded_keywords = {
            'developer', 'engineer', 'manager', 'analyst', 'specialist', 'associate',
            'professor', 'instructor', 'teacher', 'student', 'intern', 'internship',
            'objective', 'career objective', 'professional summary', 'contact', 'phone',
            'email', 'address', 'portfolio', 'linkedin', 'github',
        }
        normalized_candidate = candidate.lower().rstrip(':')
        if not candidate or '@' in candidate or len(candidate) > 80 or any(char.isdigit() for char in candidate):
            return False
        if normalized_candidate in excluded_tokens:
            return False
        if any(keyword in normalized_candidate for keyword in excluded_keywords):
            return False
        # Reject skill names and tech phrases (e.g. 'Django Framework', 'Machine Learning')
        if self._looks_like_skill_not_name(candidate):
            return False

        words = candidate.split()
        if not (1 < len(words) < 6):
            return False
        return all(word[:1].isupper() for word in words if any(char.isalpha() for char in word))

    def _filter_ambiguous_full_text_skills(self, text: str, skills: list[str]) -> list[str]:
        filtered = []
        for skill in skills:
            if skill == 'Information Technology':
                if not re.search(r'\binformation technology\b', text, re.IGNORECASE):
                    continue
            elif skill == 'Computer Science':
                if not re.search(r'\bcomputer science\b', text, re.IGNORECASE):
                    continue
            elif skill == 'Go':
                if not re.search(r'\bgolang\b|\bgo language\b', text, re.IGNORECASE):
                    continue
            elif skill == 'AI':
                if not re.search(r'\bartificial intelligence\b', text, re.IGNORECASE) and not re.search(r'(?<![A-Za-z0-9])AI(?![A-Za-z0-9])', text):
                    continue
            elif skill == 'Machine Learning':
                if not re.search(r'\bmachine learning\b', text, re.IGNORECASE) and not re.search(r'(?<![A-Za-z0-9])ML(?![A-Za-z0-9])', text):
                    continue
            elif skill == 'JavaScript':
                if not re.search(r'\bjavascript\b', text, re.IGNORECASE) and not re.search(r'(?<![A-Za-z0-9])JS(?![A-Za-z0-9])', text):
                    continue
            elif skill == 'TypeScript':
                if not re.search(r'\btypescript\b', text, re.IGNORECASE) and not re.search(r'(?<![A-Za-z0-9])TS(?![A-Za-z0-9])', text):
                    continue
            filtered.append(skill)
        return filtered

    def _filter_education_domain_skills(self, text: str, skills: list[str]) -> list[str]:
        filtered = []
        for skill in skills:
            occurrences = [match.start() for match in re.finditer(re.escape(skill.lower()), text.lower())]
            if self._should_skip_education_domain_skill(text, skill, occurrences):
                continue
            filtered.append(skill)
        return filtered

    def _should_skip_education_domain_skill(self, text: str, skill: str, occurrences: list[int]) -> bool:
        if skill not in {'Information Technology', 'Computer Science'}:
            return False
        if not occurrences:
            return False
        return all(self._is_education_context(text, index, len(skill)) for index in occurrences)

    def _is_education_context(self, text: str, start: int, length: int) -> bool:
        window_start = max(0, start - 80)
        window_end = min(len(text), start + length + 80)
        context = text[window_start:window_end].lower()
        education_markers = (
            'bachelor', 'master', 'phd', 'doctorate', 'degree', 'graduate',
            'bsit', 'bscs', 'bs cs', 'bs it', 'it/cs', 'cs/it', 'it-related',
            'educational', 'qualification', 'qualifications', 'minimum qualifications',
            'college', 'university', 'diploma', 'academic background', 'academic qualifications',
        )
        return any(marker in context for marker in education_markers)

    def _extract_first_section_text(self, text: str, section_names: list[str], stop_names: list[str], leading_context_lines: int = 0) -> str:
        lines = text.splitlines()
        start_index = None
        section_heading_index = None
        for index, line in enumerate(lines):
            normalized = ' '.join(line.strip().lower().rstrip(':').split())
            if normalized in section_names:
                start_index = index + 1
                section_heading_index = index
                break

        if start_index is None:
            return ''

        collected = []
        if section_heading_index is not None and leading_context_lines > 0:
            leading_lines = []
            context_start = max(0, section_heading_index - leading_context_lines)
            for line in reversed(lines[context_start:section_heading_index]):
                stripped = ' '.join(line.split()).strip()
                if not stripped:
                    if leading_lines:
                        break
                    continue
                normalized = stripped.lower().rstrip(':')
                if normalized in stop_names or normalized in section_names:
                    break
                leading_lines.append(stripped)
            collected.extend(reversed(leading_lines))

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

    def _extract_ranked_education_lines(self, text: str, limit: int) -> list[str]:
        keywords = ['university', 'college', 'institute', 'school', 'academy', 'bachelor', 'master', 'phd', 'mba', 'degree', 'diploma']
        candidate_lines = []
        lines = text.splitlines()
        for index, raw_line in enumerate(lines):
            line = ' '.join(raw_line.split())
            line = re.sub(r'^[•\-*\d.)\s]+', '', line).strip()
            if not line or not any(keyword in line.lower() for keyword in keywords):
                continue
            if not (10 < len(line) < 200):
                continue
            context = ' '.join(' '.join(part.split()).strip() for part in lines[index:index + 3] if part.strip())
            score = self._score_recency_context(context)
            candidate_lines.append((score, index, line))

        ranked_lines = [line for _, _, line in sorted(candidate_lines, key=lambda item: (-item[0], item[1]))]
        return self._dedupe_lines(ranked_lines)[:limit]

    def _extract_ranked_experience_title_lines(self, text: str, limit: int) -> list[str]:
        title_keywords = [
            'developer', 'engineer', 'analyst', 'manager', 'lead', 'specialist', 'assistant', 'associate',
            'attendant', 'barista', 'bartender', 'bellman', 'busser', 'butler', 'cashier', 'clerk',
            'coordinator', 'customer service', 'front desk', 'host', 'housekeeping', 'officer', 'operations',
            'receptionist', 'recruiter', 'representative', 'sales', 'scheduler', 'secretary', 'server',
            'staff', 'supervisor', 'teacher', 'trainer', 'helper', 'kitchen', 'fitness', 'support', 'consultant',
        ]
        candidate_lines = []
        lines = text.splitlines()
        for index, raw_line in enumerate(lines):
            normalized_line = ' '.join(raw_line.split()).strip()
            if not normalized_line:
                continue
            cleaned = re.sub(r'^[•\-*\d.)\s]+', '', normalized_line).strip()
            if cleaned != normalized_line:
                continue
            if not (8 < len(cleaned) < 140):
                continue
            lowered = cleaned.lower()
            if not any(keyword in lowered for keyword in title_keywords):
                continue
            context = ' '.join(' '.join(part.split()).strip() for part in lines[index:index + 3] if part.strip())
            score = self._score_recency_context(context)
            candidate_lines.append((score, index, cleaned))

        ranked_lines = [line for _, _, line in sorted(candidate_lines, key=lambda item: (-item[0], item[1]))]
        return self._dedupe_lines(ranked_lines)[:limit]

    def _score_recency_context(self, text: str) -> int:
        context = (text or '').lower()
        years = [int(year) for year in re.findall(r'\b(19\d{2}|20\d{2})\b', context)]
        latest_year = max(years) if years else 0
        present_bonus = 10000 if 'present' in context or 'current' in context else 0
        return present_bonus + latest_year

    def _dedupe_lines(self, lines: list[str]) -> list[str]:
        deduped = []
        seen = set()
        for line in lines:
            key = line.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(line)
        return deduped

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