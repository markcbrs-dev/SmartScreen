"""
main/services/job_parser.py — Extracts structured data from uploaded job description files.

OVERVIEW
--------
JobDescriptionParser.parse() is the main entry point.
It accepts a file-like object (TXT or DOCX) and returns a JobProfile dataclass.

EXTRACTION PIPELINE
-------------------
  1. extract_text()               — reads raw text (TXT: charset fallback chain; DOCX: python-docx)
  2. extract_job_title()          — regex label patterns → first-line role keyword heuristic → filename
  3. extract_company()            — regex + optional spaCy ORG entity
  4. extract_location()           — regex + optional spaCy GPE entity
  5. extract_employment_type()    — keyword list match
  6. extract_experience_level()   — keyword list match
  7. extract_salary_range()       — regex patterns
  8. extract_remote_option()      — keyword detection
  9. extract_skills()             — section-based and full-text skill keyword extraction
 10. extract_responsibilities()   — section-based bullet extraction
 11. extract_qualifications()     — section-based bullet extraction
 12. extract_education_requirements()— section-based keyword extraction
 13. extract_certifications()     — section-based keyword extraction
 14. extract_benefits()           — section-based bullet extraction
 15. extract_industries()         — industry keyword match

HOW TO MODIFY
-------------
  Add a new skill keyword       → add to _load_tech_skills() or _load_soft_skills()
                                   Keep in sync with _SKILL_VOCAB in matcher.py and
                                   _load_common_skills() in resume_parser.py
  Add a new industry            → add to _load_industries()
  Change title extraction       → edit extract_job_title() (regex patterns list + role_keywords)
  Add a new section to parse    → add regex section heading patterns and a new extract_*() method
  Change hard limits            → edit MAX_TEXT_LENGTH, MAX_DOCX_PARAGRAPHS, MAX_DOCX_TABLE_ROWS
  Enable spaCy NER for company/location → set SMARTSCREEN_ENABLE_SPACY=1 env var
  Change display formatting     → edit _format_docx_display_paragraph() for DOCX bold handling
  Change preview text extraction→ edit extract_description_preview_text() and
                                   extract_about_job_onward_text()

DEBUG
-----
  Title parsed as None?         → check extract_job_title(); may need a new regex pattern
  Skills always empty?          → check _load_tech_skills()/_load_soft_skills() vocabulary
  Wrong section parsed?         → check _extract_section_items() heading pattern matching
  DOCX text garbled?            → check extract_text_from_docx() / _format_docx_paragraph()
"""

import importlib
import logging
import os
import re
from html import escape
from typing import Optional

from .entities import JobProfile
from .skills import (
    extract_canonical_skills,
    get_canonical_skills,
    normalize_education_entries,
    normalize_employment_type,
    normalize_experience_level,
    normalize_list_text,
    normalize_location_text,
    normalize_title_text,
    normalize_whitespace,
)

logger = logging.getLogger(__name__)

try:
    from docx import Document
except ImportError:
    Document = None

try:
    spacy = importlib.import_module('spacy')
except ImportError:
    spacy = None


ENABLE_SPACY = os.environ.get('SMARTSCREEN_ENABLE_SPACY', '').strip().lower() in {'1', 'true', 'yes', 'on'}


class JobDescriptionParser:
    MAX_TEXT_LENGTH = 50000       # Hard cap on raw text stored; raise if JDs are being silently truncated
    MAX_DOCX_PARAGRAPHS = 250     # Stop reading DOCX paragraphs after this many; raise if content is cut off
    MAX_DOCX_TABLE_ROWS = 150     # Stop reading DOCX table rows after this many

    def __init__(self, nlp_model: str = 'en_core_web_sm'):
        self._nlp = None               # Lazy-loaded spaCy model; loaded only when ENABLE_SPACY=1
        self._nlp_load_failed = False  # Flag to avoid retrying a failed model load on every call
        self._nlp_model = nlp_model    # Change the NER model name by passing a different value here
        self.tech_skills = self._load_tech_skills()           # Modify _load_tech_skills() to add/remove tech skill keywords
        self.soft_skills = self._load_soft_skills()           # Modify _load_soft_skills() to add/remove soft skill keywords
        self.employment_types = self._load_employment_types() # Modify _load_employment_types() to recognise new contract types
        self.experience_levels = self._load_experience_levels() # Modify _load_experience_levels() for new seniority labels
        self.industries = self._load_industries()             # Modify _load_industries() to add new industry keywords

    def parse(self, file_obj, filename: Optional[str] = None) -> JobProfile | list[JobProfile]:
        raw_text = self.extract_text(file_obj, filename=filename)
        return self.parse_text(raw_text, filename=filename)

    def parse_text(self, text: str, filename: Optional[str] = None) -> JobProfile | list[JobProfile]:
        segments = self._split_job_documents(text)
        profiles = [self._parse_single_text(segment, filename=filename) for segment in segments if segment.strip()]
        if not profiles:
            return self._parse_single_text(text, filename=filename)
        if len(profiles) == 1:
            return profiles[0]
        return profiles

    def _parse_single_text(self, raw_text: str, filename: Optional[str] = None) -> JobProfile:
        required_skills, preferred_skills = self.extract_skills(raw_text)
        title = self.extract_job_title(raw_text, filename=filename)
        return JobProfile(
            title=normalize_title_text(title) if title else None,
            reporting_to=self.extract_reporting_to(raw_text),
            company=self.extract_company(raw_text),
            location=self.extract_location(raw_text),
            employment_type=self.extract_employment_type(raw_text),
            experience_level=self.extract_experience_level(raw_text),
            salary_range=self.extract_salary_range(raw_text),
            remote_option=self.extract_remote_option(raw_text),
            required_skills=required_skills,
            preferred_skills=preferred_skills,
            responsibilities=self.extract_responsibilities(raw_text),
            qualifications=self.extract_qualifications(raw_text),
            education_requirements=normalize_education_entries(self.extract_education_requirements(raw_text)),
            certifications=self.extract_certifications(raw_text),
            benefits=self.extract_benefits(raw_text),
            industries=self.extract_industries(raw_text),
            raw_text=raw_text,
        )

    def extract_text(self, file_obj, filename: Optional[str] = None) -> str:
        ext = os.path.splitext(filename or getattr(file_obj, 'name', ''))[1].lower()
        if ext == '.txt':
            return self.extract_text_from_txt(file_obj)
        if ext == '.docx':
            return self.extract_text_from_docx(file_obj)
        raise ValueError('Unsupported job description format. Please upload a TXT or DOCX file.')

    def extract_display_text(self, file_obj, filename: Optional[str] = None) -> str:
        ext = os.path.splitext(filename or getattr(file_obj, 'name', ''))[1].lower()
        if ext == '.txt':
            return self.extract_text_from_txt(file_obj)
        if ext == '.docx':
            return self.extract_display_text_from_docx(file_obj)
        raise ValueError('Unsupported job description format. Please upload a TXT or DOCX file.')

    def extract_text_from_docx(self, file_obj) -> str:
        if Document is None:
            raise ValueError('DOCX support is not installed. Add python-docx to parse job descriptions.')
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
        try:
            document = Document(file_obj)
        except Exception as exc:
            logger.exception('python-docx failed to extract job description text.')
            raise ValueError(f'Could not read the uploaded DOCX: {exc}') from exc

        lines = []
        for paragraph in document.paragraphs[:self.MAX_DOCX_PARAGRAPHS]:
            formatted_paragraph = self._format_docx_paragraph(paragraph)
            if formatted_paragraph:
                lines.append(formatted_paragraph)
            if self._joined_length(lines) >= self.MAX_TEXT_LENGTH:
                break

        if self._joined_length(lines) < self.MAX_TEXT_LENGTH:
            row_count = 0
            for table in document.tables:
                for row in table.rows:
                    row_count += 1
                    if row_count > self.MAX_DOCX_TABLE_ROWS:
                        break
                    for cell in row.cells:
                        if cell.text.strip():
                            lines.append(cell.text.strip())
                        if self._joined_length(lines) >= self.MAX_TEXT_LENGTH:
                            break
                    if self._joined_length(lines) >= self.MAX_TEXT_LENGTH:
                        break
                if row_count > self.MAX_DOCX_TABLE_ROWS or self._joined_length(lines) >= self.MAX_TEXT_LENGTH:
                    break

        return self._truncate_text('\n'.join(lines))

    def extract_display_text_from_docx(self, file_obj) -> str:
        if Document is None:
            raise ValueError('DOCX support is not installed. Add python-docx to parse job descriptions.')
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
        try:
            document = Document(file_obj)
        except Exception as exc:
            logger.exception('python-docx failed to extract formatted job description text.')
            raise ValueError(f'Could not read the uploaded DOCX: {exc}') from exc

        lines = []
        for paragraph in document.paragraphs[:self.MAX_DOCX_PARAGRAPHS]:
            formatted_paragraph = self._format_docx_display_paragraph(paragraph)
            if formatted_paragraph:
                lines.append(formatted_paragraph)
            if self._joined_length(lines) >= self.MAX_TEXT_LENGTH:
                break

        if self._joined_length(lines) < self.MAX_TEXT_LENGTH:
            row_count = 0
            for table in document.tables:
                for row in table.rows:
                    row_count += 1
                    if row_count > self.MAX_DOCX_TABLE_ROWS:
                        break
                    for cell in row.cells:
                        if cell.text.strip():
                            lines.append(cell.text.strip())
                        if self._joined_length(lines) >= self.MAX_TEXT_LENGTH:
                            break
                    if self._joined_length(lines) >= self.MAX_TEXT_LENGTH:
                        break
                if row_count > self.MAX_DOCX_TABLE_ROWS or self._joined_length(lines) >= self.MAX_TEXT_LENGTH:
                    break

        return self._truncate_text('\n'.join(lines))

    def extract_text_from_txt(self, file_obj) -> str:
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
        content = file_obj.read(self.MAX_TEXT_LENGTH + 1)  # Read 1 byte extra so _truncate_text can detect overflow
        if isinstance(content, bytes):
            # Try common encodings in order; most files are UTF-8, but older Windows-saved files use cp1252
            for encoding in ('utf-8', 'latin-1', 'cp1252'):
                try:
                    return self._truncate_text(content.decode(encoding))
                except UnicodeDecodeError:
                    continue
            raise ValueError('Could not decode the uploaded text file.')
        return self._truncate_text(str(content))

    def extract_job_title(self, text: str, filename: Optional[str] = None) -> Optional[str]:
        # Add new job role words here if titles aren't being detected from first-line heuristic
        role_keywords = {
            'professor',
            'engineer',
            'developer',
            'analyst',
            'manager',
            'specialist',
            'consultant',
            'scientist',
            'designer',
            'administrator',
            'coordinator',
            'assistant',
            'associate',
            'attendant',
            'barista',
            'bartender',
            'bellman',
            'busser',
            'bussing',
            'butler',
            'cashier',
            'clerk',
            'crew',
            'executive',
            'egress',
            'fitness',
            'host',
            'helper',
            'housekeeper',
            'ingress',
            'instructor',
            'kitchen',
            'officer',
            'person',
            'receptionist',
            'recruiter',
            'representative',
            'salesperson',
            'secretary',
            'server',
            'staff',
            'support',
            'supervisor',
            'teacher',
            'lecturer',
            'trainer',
        }

        # Priority 1: look for an explicit label like 'Job Title: ...' in the first 25 lines
        # Add a new pattern here if your JD format uses a different heading
        patterns = [
            r'Job Title:\s*(.+)',
            r'Position:\s*(.+)',
            r'Title:\s*(.+)',
            r'Role:\s*(.+)',
        ]
        for line in text.splitlines()[:25]:
            candidate = line.strip()
            if len(candidate) > 120:
                continue
            for pattern in patterns:
                match = re.search(pattern, candidate, re.IGNORECASE)
                if match:
                    return self._normalize_title_candidate(
                        (match.group(1) if match.lastindex else match.group(0)).strip(),
                        role_keywords,
                    )

        # Priority 2: scan for hiring language like 'seeking a Senior Developer'
        summary_match = re.search(
            r'\b(?:seeking|hiring|looking for)\b[^.\n]{0,80}?\b([A-Z][A-Za-z&/\- ]{2,80}\b(?:Professor|Engineer|Developer|Analyst|Manager|Specialist|Consultant|Scientist|Designer|Administrator|Coordinator|Assistant|Associate|Attendant|Barista|Bartender|Bellman|Busser|Bussing\s+Person|Butler|Cashier|Clerk|Executive|Gym\s+Attendant|Host|Housekeeper|Ingress\s*&\s*Egress\s+Support|Kitchen\s+Helper|Officer|Receptionist|Recruiter|Representative|Secretary|Server|Staff|Supervisor|Teacher|Trainer))\b',
            text,
            re.IGNORECASE,
        )
        if summary_match:
            return self._normalize_title_candidate(summary_match.group(1), role_keywords)

        # Priority 3: scan first 40 lines for any line containing a known role keyword
        for line in text.splitlines()[:40]:
            candidate = ' '.join(line.split()).strip(':- ')
            if not candidate or len(candidate) > 120:
                continue
            words = [word.strip('.,()').lower() for word in candidate.split()]
            if any(word in role_keywords for word in words):
                return self._normalize_title_candidate(candidate, role_keywords)

        # Priority 4: infer the title from the filename only if the text didn't produce one
        filename_candidate = self._extract_title_from_filename(filename, role_keywords)
        if filename_candidate:
            return filename_candidate
        return None  # Title could not be inferred; will fall back to filename stem in replace_active_job_description()

    def extract_company(self, text: str) -> Optional[str]:
        # Keep only specific header patterns; the broad 'About\s+(.+)' pattern
        # was too greedy and would capture section titles like 'About the Role'
        # as the company name.  NER handles the fallback reliably.
        for pattern in [r'Company:\s*(.+)', r'Organization:\s*(.+)']:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if len(match.strip()) > 2:
                    return normalize_whitespace(match)
        nlp = self._get_nlp()
        if nlp is not None:
            doc = nlp(text[:1000])
            for entity in doc.ents:
                if entity.label_ == 'ORG' and len(entity.text.strip()) > 2:
                    return normalize_whitespace(entity.text)
        return None

    def extract_reporting_to(self, text: str) -> Optional[str]:
        patterns = [
            r'Reporting\s+To\s*:\s*(.+)',
            r'Reports?\s+To\s*:\s*(.+)',
            r'Supervisor\s*:\s*(.+)',
        ]
        lines = text.splitlines()[:60]
        for index, line in enumerate(lines):
            candidate = line.strip()
            if not candidate or len(candidate) > 160:
                continue
            for pattern in patterns:
                match = re.search(pattern, candidate, re.IGNORECASE)
                if match:
                    value = normalize_whitespace(match.group(1))
                    if value:
                        return value.title()
            if re.fullmatch(r'(?:Reporting\s+To|Reports?\s+To|Supervisor)\s*:', candidate, re.IGNORECASE):
                for next_line in lines[index + 1:]:
                    value = normalize_whitespace(next_line)
                    if value:
                        return value.title()
        return None

    def extract_location(self, text: str) -> Optional[str]:
        for pattern in [r'Location:\s*(.+)', r'Work Location:\s*(.+)', r'Based in:\s*(.+)']:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if len(match.strip()) > 2:
                    return normalize_location_text(match)
        nlp = self._get_nlp()
        if nlp is not None:
            doc = nlp(text[:1500])
            # Include both GPE (cities, countries) and LOC (e.g. 'Metro Manila',
            # 'Silicon Valley') so regional area names are not missed.
            locations = [entity.text.strip() for entity in doc.ents if entity.label_ in {'GPE', 'LOC'}]
            if locations:
                return normalize_location_text(', '.join(dict.fromkeys(locations)))
        return None

    def _get_nlp(self):
        if self._nlp is not None:
            return self._nlp
        if self._nlp_load_failed or spacy is None or not ENABLE_SPACY:
            return None
        try:
            self._nlp = spacy.load(self._nlp_model)
        except OSError:
            logger.info('spaCy model %s is not installed; using regex-based job parsing.', self._nlp_model)
            self._nlp_load_failed = True
            return None
        return self._nlp

    def extract_employment_type(self, text: str) -> Optional[str]:
        text_lower = text.lower()
        combined_patterns = [
            r'\bfull\s*[- ]time\s*(?:or|/|and)\s*part\s*[- ]time\b',
            r'\bpart\s*[- ]time\s*(?:or|/|and)\s*full\s*[- ]time\b',
        ]
        if any(re.search(pattern, text_lower) for pattern in combined_patterns):
            return normalize_employment_type('full-time or part-time')
        for item in self.employment_types:
            if re.search(r'\b' + re.escape(item) + r'\b', text_lower):
                return normalize_employment_type(item)
        return None

    def extract_experience_level(self, text: str) -> Optional[str]:
        text_lower = text.lower()
        # First try to match a known seniority label (e.g. 'senior', 'junior') from experience_levels vocabulary
        for item in self.experience_levels:
            if re.search(r'\b' + re.escape(item) + r'\b', text_lower):
                return normalize_experience_level(item)
        # Fallback: infer level from years of experience mentioned in the JD
        # Modify the year thresholds here if the mapping feels off for your domain
        years_match = re.search(r'(\d+)\s*[-+]?\s*years?\s*(?:of)?\s*experience', text, re.IGNORECASE)
        if years_match:
            years = int(years_match.group(1))
            if years <= 2:
                return normalize_experience_level('entry level')
            if years <= 5:
                return normalize_experience_level('mid level')
            return normalize_experience_level('senior')
        return None  # No level detected; matcher will treat job as mid-level by default

    def extract_salary_range(self, text: str) -> Optional[str]:
        patterns = [
            r'Salary:\s*(\$?[\d,]+(?:\.\d{2})?\s*[-–—]\s*\$?[\d,]+(?:\.\d{2})?)',
            r'Compensation:\s*(\$?[\d,]+(?:\.\d{2})?\s*[-–—]\s*\$?[\d,]+(?:\.\d{2})?)',
            r'(\$\d+(?:\.\d{2})?\s*(?:to|-|–|—)\s*\$\d+(?:\.\d{2})?)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def extract_remote_option(self, text: str) -> bool:
        text_lower = text.lower()
        remote_keywords = ['remote', 'work from home', 'wfh', 'hybrid', 'distributed']
        return any(keyword in text_lower for keyword in remote_keywords)

    def extract_skills(self, text: str) -> tuple[list[str], list[str]]:
        text_lower = text.lower()
        required_section_text = self._join_section_texts(
            text,
            [
                ['requirements', 'minimum requirements', 'required skills', 'skills required'],
                ['qualifications', 'minimum qualifications', 'must have'],
            ],
            ['preferred qualifications', 'preferred skills', 'nice to have', 'bonus', 'benefits', 'responsibilities'],
        )
        preferred_section_text = self._join_section_texts(
            text,
            [
                ['preferred qualifications', 'preferred skills', 'desired skills'],
                ['nice to have', 'bonus', 'plus'],
            ],
            ['requirements', 'minimum requirements', 'responsibilities', 'benefits'],
        )

        required = extract_canonical_skills(required_section_text) if required_section_text else []
        preferred = extract_canonical_skills(preferred_section_text) if preferred_section_text else []

        if not required and not preferred:
            all_skills = extract_canonical_skills(text)
            for skill in all_skills:
                occurrences = [match.start() for match in re.finditer(re.escape(skill.lower()), text_lower)]
                if any(self._is_preferred_context(text_lower, index, len(skill)) for index in occurrences):
                    preferred.append(skill)
                else:
                    required.append(skill)

        required = normalize_list_text(required)
        preferred = [skill for skill in normalize_list_text(preferred) if skill not in required]
        return required, preferred

    def extract_responsibilities(self, text: str) -> list[str]:
        return self._extract_section_items(text, ['responsibilities', 'duties', 'what you will do'], limit=10)

    def extract_qualifications(self, text: str) -> list[str]:
        return self._extract_section_items(text, ['qualifications', 'requirements', 'must have'], limit=10)

    def extract_description_preview_text(self, text: str) -> str:
        about_section = self._extract_named_section_text(
            text,
            ['about the job', 'about this job'],
            ['job description', 'description', 'role description', 'qualifications', 'requirements', 'education', 'responsibilities', 'duties', 'benefits'],
        )
        if about_section:
            return about_section
        return self.extract_job_description_section(text)

    def extract_about_job_onward_text(self, text: str) -> str:
        lines = text.splitlines()
        for index, line in enumerate(lines):
            normalized = self._normalize_section_line(line)
            if normalized in {'about the job', 'about this job'}:
                return '\n'.join(lines[index:]).strip()
        return self.extract_description_preview_text(text)

    def extract_job_description_section(self, text: str) -> str:
        section = self._extract_named_section_text(
            text,
            ['job description', 'description', 'role description'],
            ['qualifications', 'requirements', 'education', 'responsibilities', 'duties', 'benefits'],
        )
        return section or text.strip()

    def extract_education_requirements(self, text: str) -> list[str]:
        keywords = ['education', 'degree', 'bachelor', 'master', 'phd', 'diploma']
        section_text = self._join_section_texts(
            text,
            [
                ['education', 'educational requirements'],
                ['qualifications', 'requirements', 'minimum qualifications'],
            ],
            ['responsibilities', 'benefits', 'preferred qualifications'],
        )
        if section_text:
            lines = []
            for sentence in re.split(r'[.!?\n]', section_text):
                candidate = ' '.join(sentence.split())
                if any(keyword in candidate.lower() for keyword in keywords) and 10 < len(candidate) < 160:
                    lines.append(candidate)
            if lines:
                return lines[:5]
        lines = []
        for sentence in re.split(r'[.!?\n]', text):
            candidate = ' '.join(sentence.split())
            if any(keyword in candidate.lower() for keyword in keywords) and 10 < len(candidate) < 160:
                lines.append(candidate)
        return lines[:5]

    def extract_certifications(self, text: str) -> list[str]:
        # Add new certifications here; they are matched as simple substrings (case-insensitive)
        results = []
        for cert in ['pmp', 'aws certified', 'microsoft certified', 'cisco', 'scrum master', 'security+']:
            if cert in text.lower():
                results.append(cert.title())
        return results

    def extract_benefits(self, text: str) -> list[str]:
        lines = self._extract_section_items(text, ['benefits', 'perks', 'what we offer'], limit=8)
        common_benefits = ['health insurance', 'retirement plan', 'paid time off', 'flexible hours', 'remote work']
        for benefit in common_benefits:
            if benefit in text.lower() and benefit.title() not in lines:
                lines.append(benefit.title())
        return lines[:8]

    def extract_industries(self, text: str) -> list[str]:
        text_lower = text.lower()
        industries = {industry.title() for industry in self.industries if re.search(r'\b' + re.escape(industry) + r'\b', text_lower)}
        if any(token in text_lower for token in ('hotel', 'guest', 'lounge', 'buffet', 'bar')):
            industries.add('Hospitality')
        return sorted(industries)

    def _extract_section_items(self, text: str, keywords: list[str], limit: int) -> list[str]:
        items = []
        lines = text.splitlines()
        for index, line in enumerate(lines):
            normalized = line.strip().lower()
            if not normalized:
                continue
            # Scan for a line that contains one of the section heading keywords
            if len(normalized) <= 80 and any(keyword in normalized for keyword in keywords):
                # Collect up to 7 lines after the heading as section items
                # Stops early on a blank line; increase index + 8 if your JDs have longer sections
                for next_line in lines[index + 1:index + 8]:
                    cleaned = self._normalize_section_item(next_line)
                    if not cleaned:
                        break  # Blank line = end of section
                    if 8 < len(cleaned) < 220:  # Ignore lines that are too short or too long to be items
                        items.append(cleaned)
                if items:
                    break  # Stop after the first matching section
        return list(dict.fromkeys(items))[:limit]  # dict.fromkeys preserves order and deduplicates

    def _normalize_section_item(self, line: str) -> str:
        stripped = line.strip()
        if not stripped:
            return ''

        bullet_match = re.match(r'^([•\-*])\s*(.+)$', stripped)
        if bullet_match:
            marker, content = bullet_match.groups()
            content = content.strip()
            return f'{marker} {content}' if content else ''

        numbered_match = re.match(r'^\d+[.)]\s*(.+)$', stripped)
        if numbered_match:
            return numbered_match.group(1).strip()

        return stripped

    def _extract_named_section_text(self, text: str, section_names: list[str], stop_names: list[str]) -> str:
        lines = text.splitlines()
        start_index = None
        for index, line in enumerate(lines):
            normalized = self._normalize_section_line(line)
            if any(normalized == name for name in section_names):
                start_index = index + 1
                break
            for name in section_names:
                prefix = f'{name}:'
                if normalized.startswith(prefix):
                    remainder = line.strip()[len(prefix):].strip()
                    remaining_lines = [remainder] if remainder else []
                    remaining_lines.extend(lines[index + 1:])
                    return self._collect_section_lines(remaining_lines, stop_names)

        if start_index is None:
            return ''
        return self._collect_section_lines(lines[start_index:], stop_names)

    def _join_section_texts(self, text: str, section_groups: list[list[str]], stop_names: list[str]) -> str:
        sections = []
        for section_names in section_groups:
            section_text = self._extract_named_section_text(text, section_names, stop_names)
            if section_text:
                sections.append(section_text)
        return '\n'.join(sections).strip()

    def _collect_section_lines(self, lines: list[str], stop_names: list[str]) -> str:
        collected = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if collected:
                    collected.append('')
                continue

            normalized = self._normalize_section_line(stripped)
            if stop_names and any(normalized == name or normalized.startswith(f'{name}:') for name in stop_names):
                break

            collected.append(stripped)

        return '\n'.join(collected).strip()

    def _format_docx_paragraph(self, paragraph) -> str:
        text = paragraph.text.strip()
        if not text:
            return ''
        if self._is_bullet_paragraph(paragraph) and not re.match(r'^[•\-*]', text):
            return f'• {text}'
        return text

    def _format_docx_display_paragraph(self, paragraph) -> str:
        text = paragraph.text.strip()
        if not text:
            return ''

        formatted_runs = []
        for run in paragraph.runs:
            run_text = run.text
            if not run_text:
                continue
            # Wrap bold runs in ** so views.py _render_inline_formatting() can convert them to <strong>
            if run.bold:
                formatted_runs.append(f'**{run_text}**')
            else:
                formatted_runs.append(run_text)

        formatted_text = ''.join(formatted_runs).strip() or text
        # Prepend bullet symbol for DOCX 'List Bullet' style paragraphs that don't already have one
        if self._is_bullet_paragraph(paragraph) and not re.match(r'^[•\-*]', formatted_text.lstrip()):
            return f'• {formatted_text}'
        return formatted_text

    def _is_bullet_paragraph(self, paragraph) -> bool:
        style_name = getattr(getattr(paragraph, 'style', None), 'name', '') or ''
        lowered = style_name.lower()
        if 'list bullet' in lowered or 'bullet' in lowered:
            return True

        paragraph_properties = getattr(getattr(paragraph, '_p', None), 'pPr', None)
        if paragraph_properties is not None and getattr(paragraph_properties, 'numPr', None) is not None:
            return True

        return False

    def _normalize_section_line(self, line: str) -> str:
        return re.sub(r'\*\*(.*?)\*\*', r'\1', line).strip().rstrip(':').lower()

    def _is_preferred_context(self, text: str, start: int, length: int) -> bool:
        # Looks ±50 characters around the skill match for 'preferred', 'nice to have', etc.
        # Add new preferred-context markers to this list if skills are being misclassified as required
        window_start = max(0, start - 50)
        window_end = min(len(text), start + length + 50)
        context = text[window_start:window_end]
        return any(marker in context for marker in ['preferred', 'nice to have', 'bonus', 'plus', 'desired'])

    def _load_tech_skills(self) -> set[str]:
        return set(get_canonical_skills('hard'))

    def _load_soft_skills(self) -> set[str]:
        return set(get_canonical_skills('soft'))

    def _load_employment_types(self) -> set[str]:
        return {'full-time', 'part-time', 'contract', 'temporary', 'internship', 'hybrid', 'remote', 'onsite',
                'freelance', 'permanent', 'seasonal', 'volunteer', 'commission'
        }

    def _load_experience_levels(self) -> set[str]:
        return {'entry level', 'junior', 'mid level', 'senior', 'lead', 'principal', 'manager', 'director',
                'intern', 'trainee', 'associate', 'staff', 'vp', 'c-level'
        }

    def _load_industries(self) -> set[str]:
        return {
            'technology', 'software', 'it', 'finance', 'banking', 'healthcare', 'education', 'retail',
            'manufacturing', 'consulting', 'marketing', 'media', 'real estate', 'insurance', 'saas', 'admin',
            'hospitality', 'tourism', 'food service', 'customer service', 'human resources', 'recruitment',
            'bpo', 'call center', 'travel', 'property management', 'hotel', 'restaurant', 'food and beverage',
            'fitness', 'recreation',
            'administrator', 'coordinator', 'professor', 'engineer', 'developer', 'analyst', 'specialist',
            'programmer', 'support', 'human resources', 'hr', 'operations', 'logistics'
        }

    def _truncate_text(self, text: str) -> str:
        return text[:self.MAX_TEXT_LENGTH]

    def _joined_length(self, lines: list[str]) -> int:
        return sum(len(line) for line in lines)

    def _normalize_title_candidate(self, candidate: str, role_keywords: set[str]) -> str:
        words = [word for word in candidate.split() if word]
        if not words:
            return candidate.strip()

        role_index = None
        for index, word in enumerate(words):
            normalized = word.strip('.,()').lower()
            if normalized in role_keywords:
                role_index = index

        if role_index is None:
            return ' '.join(words).strip()

        start = max(0, role_index - 3)
        while start < role_index and not words[start][:1].isupper():
            start += 1
        return ' '.join(words[start:role_index + 1]).strip(':- ')

    def _extract_title_from_filename(self, filename: Optional[str], role_keywords: set[str]) -> Optional[str]:
        if not filename:
            return None

        stem = os.path.splitext(os.path.basename(filename))[0]
        candidate = re.sub(r'[_\-]+', ' ', stem).strip()
        if not candidate:
            return None

        normalized_candidate = candidate.lower()
        if normalized_candidate in {'uploaded job description', 'job description', 'new active role', 'new role'}:
            return None

        words = [word.strip('.,()').lower() for word in candidate.split()]
        if not any(word in role_keywords for word in words):
            return None

        return self._normalize_title_candidate(candidate.title(), role_keywords)

    def _split_job_documents(self, text: str) -> list[str]:
        lines = text.splitlines()
        blocks = []
        current_block = []

        for index, line in enumerate(lines):
            candidate = line.strip()
            next_nonempty = ''
            for lookahead in lines[index + 1:]:
                if lookahead.strip():
                    next_nonempty = lookahead.strip()
                    break

            if self._starts_new_job_block(candidate, next_nonempty) and self._has_substantial_job_content(current_block):
                blocks.append('\n'.join(current_block).strip())
                current_block = []

            current_block.append(line)

        if any(item.strip() for item in current_block):
            blocks.append('\n'.join(current_block).strip())

        return [block for block in blocks if block]

    def _starts_new_job_block(self, line: str, next_nonempty: str) -> bool:
        normalized = self._normalize_section_line(line)
        next_normalized = self._normalize_section_line(next_nonempty)
        if not normalized:
            return False

        if normalized == 'job description':
            return next_normalized in {'job title', 'position', 'title', 'role'}

        if re.match(r'^(job title|position|title|role)\s*:', line, re.IGNORECASE):
            return True

        if ':' in line:
            return False

        if next_normalized.startswith('company') or next_normalized.startswith('location') or next_normalized.startswith('employment type') or next_normalized.startswith('reporting to'):
            return True

        return False

    def _has_substantial_job_content(self, lines: list[str]) -> bool:
        normalized_lines = [self._normalize_section_line(line) for line in lines if line.strip()]
        if not normalized_lines:
            return False
        return any(
            normalized.startswith('company')
            or normalized.startswith('location')
            or normalized.startswith('employment type')
            or normalized.startswith('reporting to')
            or normalized.startswith('duties')
            or normalized.startswith('responsibilities')
            or normalized.startswith('qualifications')
            or re.match(r'^(job title|position|title|role)\s*', normalized)
            for normalized in normalized_lines
        ) and len(normalized_lines) > 2
