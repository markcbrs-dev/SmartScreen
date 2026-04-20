import logging
import os
import re
from typing import Optional

from .entities import JobProfile

logger = logging.getLogger(__name__)

try:
    from docx import Document
except ImportError:
    Document = None

try:
    import spacy
except ImportError:
    spacy = None


class JobDescriptionParser:
    def __init__(self, nlp_model: str = 'en_core_web_sm'):
        self.nlp = None
        self.tech_skills = self._load_tech_skills()
        self.soft_skills = self._load_soft_skills()
        self.employment_types = self._load_employment_types()
        self.experience_levels = self._load_experience_levels()
        self.industries = self._load_industries()
        if spacy is not None:
            try:
                self.nlp = spacy.load(nlp_model)
            except OSError:
                logger.info('spaCy model %s is not installed; using regex-based job parsing.', nlp_model)

    def parse(self, file_obj, filename: Optional[str] = None) -> JobProfile:
        raw_text = self.extract_text(file_obj, filename=filename)
        required_skills, preferred_skills = self.extract_skills(raw_text)
        return JobProfile(
            title=self.extract_job_title(raw_text),
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

        lines = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        lines.append(cell.text.strip())
        return '\n'.join(lines)

    def extract_text_from_txt(self, file_obj) -> str:
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
        content = file_obj.read()
        if isinstance(content, bytes):
            for encoding in ('utf-8', 'latin-1', 'cp1252'):
                try:
                    return content.decode(encoding)
                except UnicodeDecodeError:
                    continue
            raise ValueError('Could not decode the uploaded text file.')
        return str(content)

    def extract_job_title(self, text: str) -> Optional[str]:
        patterns = [
            r'Job Title:\s*(.+)',
            r'Position:\s*(.+)',
            r'Title:\s*(.+)',
            r'Role:\s*(.+)',
            r'\b(?:Senior|Junior|Lead|Principal)?\s*(?:[A-Z][a-z]+\s*)+(?:Engineer|Developer|Analyst|Manager|Specialist|Consultant)\b',
        ]
        for line in text.splitlines()[:25]:
            candidate = line.strip()
            if len(candidate) > 120:
                continue
            for pattern in patterns:
                match = re.search(pattern, candidate, re.IGNORECASE)
                if match:
                    return (match.group(1) if match.lastindex else match.group(0)).strip()
        return None

    def extract_company(self, text: str) -> Optional[str]:
        for pattern in [r'Company:\s*(.+)', r'Organization:\s*(.+)', r'About\s+(.+)']:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if len(match.strip()) > 2:
                    return match.strip()
        if self.nlp is not None:
            doc = self.nlp(text[:1000])
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
        if self.nlp is not None:
            doc = self.nlp(text[:1500])
            locations = [entity.text.strip() for entity in doc.ents if entity.label_ == 'GPE']
            if locations:
                return ', '.join(dict.fromkeys(locations))
        return None

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
            'agile', 'scrum', 'devops', 'rest api', 'graphql', 'microservices',
        }

    def _load_soft_skills(self) -> set[str]:
        return {
            'communication', 'teamwork', 'leadership', 'problem solving', 'critical thinking',
            'time management', 'adaptability', 'creativity', 'collaboration', 'decision making',
        }

    def _load_employment_types(self) -> set[str]:
        return {'full-time', 'part-time', 'contract', 'temporary', 'internship', 'hybrid', 'remote', 'onsite'}

    def _load_experience_levels(self) -> set[str]:
        return {'entry level', 'junior', 'mid level', 'senior', 'lead', 'principal', 'manager', 'director'}

    def _load_industries(self) -> set[str]:
        return {
            'technology', 'software', 'it', 'finance', 'banking', 'healthcare', 'education', 'retail',
            'manufacturing', 'consulting', 'marketing', 'media', 'real estate', 'insurance', 'saas',
        }