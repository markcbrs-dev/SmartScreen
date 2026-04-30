import importlib
import logging
import os
import re
from html import escape
from typing import Optional

from .entities import JobProfile

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
    MAX_TEXT_LENGTH = 50000
    MAX_DOCX_PARAGRAPHS = 250
    MAX_DOCX_TABLE_ROWS = 150

    def __init__(self, nlp_model: str = 'en_core_web_sm'):
        self._nlp = None
        self._nlp_load_failed = False
        self._nlp_model = nlp_model
        self.tech_skills = self._load_tech_skills()
        self.soft_skills = self._load_soft_skills()
        self.employment_types = self._load_employment_types()
        self.experience_levels = self._load_experience_levels()
        self.industries = self._load_industries()

    def parse(self, file_obj, filename: Optional[str] = None) -> JobProfile:
        raw_text = self.extract_text(file_obj, filename=filename)
        required_skills, preferred_skills = self.extract_skills(raw_text)
        return JobProfile(
            title=self.extract_job_title(raw_text, filename=filename),
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
            education_requirements=self.extract_education_requirements(raw_text),
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
        content = file_obj.read(self.MAX_TEXT_LENGTH + 1)
        if isinstance(content, bytes):
            for encoding in ('utf-8', 'latin-1', 'cp1252'):
                try:
                    return self._truncate_text(content.decode(encoding))
                except UnicodeDecodeError:
                    continue
            raise ValueError('Could not decode the uploaded text file.')
        return self._truncate_text(str(content))

    def extract_job_title(self, text: str, filename: Optional[str] = None) -> Optional[str]:
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
            'instructor',
            'teacher',
            'lecturer',
            'trainer',
        }

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

        filename_candidate = self._extract_title_from_filename(filename, role_keywords)
        if filename_candidate:
            return filename_candidate

        summary_match = re.search(
            r'\b(?:seeking|hiring|looking for)\b[^.\n]{0,80}?\b([A-Z][A-Za-z&/\- ]{2,80}\b(?:Professor|Engineer|Developer|Analyst|Manager|Specialist|Consultant|Scientist|Designer|Administrator|Coordinator))\b',
            text,
            re.IGNORECASE,
        )
        if summary_match:
            return self._normalize_title_candidate(summary_match.group(1), role_keywords)

        for line in text.splitlines()[:40]:
            candidate = ' '.join(line.split()).strip(':- ')
            if not candidate or len(candidate) > 120:
                continue
            words = [word.strip('.,()').lower() for word in candidate.split()]
            if any(word in role_keywords for word in words):
                return self._normalize_title_candidate(candidate, role_keywords)
        return None

    def extract_company(self, text: str) -> Optional[str]:
        # Keep only specific header patterns; the broad 'About\s+(.+)' pattern
        # was too greedy and would capture section titles like 'About the Role'
        # as the company name.  NER handles the fallback reliably.
        for pattern in [r'Company:\s*(.+)', r'Organization:\s*(.+)']:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if len(match.strip()) > 2:
                    return match.strip()
        nlp = self._get_nlp()
        if nlp is not None:
            doc = nlp(text[:1000])
            for entity in doc.ents:
                if entity.label_ == 'ORG' and len(entity.text.strip()) > 2:
                    return entity.text.strip()
        return None

    def extract_location(self, text: str) -> Optional[str]:
        for pattern in [r'Location:\s*(.+)', r'Work Location:\s*(.+)', r'Based in:\s*(.+)']:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if len(match.strip()) > 2:
                    return match.strip()
        nlp = self._get_nlp()
        if nlp is not None:
            doc = nlp(text[:1500])
            # Include both GPE (cities, countries) and LOC (e.g. 'Metro Manila',
            # 'Silicon Valley') so regional area names are not missed.
            locations = [entity.text.strip() for entity in doc.ents if entity.label_ in {'GPE', 'LOC'}]
            if locations:
                return ', '.join(dict.fromkeys(locations))
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
        for item in self.employment_types:
            if re.search(r'\b' + re.escape(item) + r'\b', text_lower):
                return item.title()
        return None

    def extract_experience_level(self, text: str) -> Optional[str]:
        text_lower = text.lower()
        for item in self.experience_levels:
            if re.search(r'\b' + re.escape(item) + r'\b', text_lower):
                return item.title()
        years_match = re.search(r'(\d+)\s*[-+]?\s*years?\s*(?:of)?\s*experience', text, re.IGNORECASE)
        if years_match:
            years = int(years_match.group(1))
            if years <= 2:
                return 'Entry Level'
            if years <= 5:
                return 'Mid Level'
            return 'Senior Level'
        return None

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
        all_skills = sorted(self.tech_skills.union(self.soft_skills))
        required = []
        preferred = []
        for skill in all_skills:
            pattern = r'\b' + re.escape(skill) + r'\b'
            if not re.search(pattern, text_lower):
                continue
            occurrences = [match.start() for match in re.finditer(pattern, text_lower)]
            if any(self._is_preferred_context(text_lower, index, len(skill)) for index in occurrences):
                preferred.append(skill.title())
            else:
                required.append(skill.title())
        return sorted(set(required)), sorted(set(preferred) - set(required))

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
        lines = []
        for sentence in re.split(r'[.!?\n]', text):
            candidate = ' '.join(sentence.split())
            if any(keyword in candidate.lower() for keyword in keywords) and 10 < len(candidate) < 160:
                lines.append(candidate)
        return lines[:5]

    def extract_certifications(self, text: str) -> list[str]:
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
        return sorted({industry.title() for industry in self.industries if re.search(r'\b' + re.escape(industry) + r'\b', text_lower)})

    def _extract_section_items(self, text: str, keywords: list[str], limit: int) -> list[str]:
        items = []
        lines = text.splitlines()
        for index, line in enumerate(lines):
            normalized = line.strip().lower()
            if not normalized:
                continue
            if any(keyword in normalized for keyword in keywords):
                for next_line in lines[index + 1:index + 8]:
                    cleaned = re.sub(r'^[•\-*\d.)\s]+', '', next_line).strip()
                    if not cleaned:
                        break
                    if 8 < len(cleaned) < 220:
                        items.append(cleaned)
                if items:
                    break
        return list(dict.fromkeys(items))[:limit]

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
            if run.bold:
                formatted_runs.append(f'**{run_text}**')
            else:
                formatted_runs.append(run_text)

        formatted_text = ''.join(formatted_runs).strip() or text
        if self._is_bullet_paragraph(paragraph) and not re.match(r'^[•\-*]', formatted_text.lstrip()):
            return f'• {formatted_text}'
        return formatted_text

    def _is_bullet_paragraph(self, paragraph) -> bool:
        style_name = getattr(getattr(paragraph, 'style', None), 'name', '') or ''
        lowered = style_name.lower()
        return 'list bullet' in lowered or 'bullet' in lowered

    def _normalize_section_line(self, line: str) -> str:
        return re.sub(r'\*\*(.*?)\*\*', r'\1', line).strip().rstrip(':').lower()

    def _is_preferred_context(self, text: str, start: int, length: int) -> bool:
        window_start = max(0, start - 50)
        window_end = min(len(text), start + length + 50)
        context = text[window_start:window_end]
        return any(marker in context for marker in ['preferred', 'nice to have', 'bonus', 'plus', 'desired'])

    def _load_tech_skills(self) -> set[str]:
        return {
            'python', 'java', 'javascript', 'typescript', 'c++', 'c#', 'ruby', 'go', 'rust',
            'html', 'css', 'react', 'angular', 'vue', 'node.js', 'express', 'django', 'flask',
            'sql', 'mongodb', 'postgresql', 'mysql', 'redis', 'aws', 'azure', 'gcp',
            'docker', 'kubernetes', 'jenkins', 'terraform', 'git', 'linux', 'machine learning',
            'deep learning', 'ai', 'data science', 'data analysis', 'tableau', 'power bi',
            'agile', 'scrum', 'devops', 'rest api', 'graphql', 'microservices', 'kotlin',
            'swift', 'flutter'
        }

    def _load_soft_skills(self) -> set[str]:
        return {
            'communication', 'teamwork', 'leadership', 'problem solving', 'critical thinking',
            'time management', 'adaptability', 'creativity', 'collaboration', 'decision making',
        }

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