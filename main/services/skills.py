from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Optional


@dataclass(frozen=True)
class SkillDefinition:
    canonical_name: str
    category: str
    aliases: tuple[str, ...]


_SKILL_DEFINITIONS: tuple[SkillDefinition, ...] = (
    SkillDefinition('Python', 'hard', ('python', 'py')),
    SkillDefinition('Java', 'hard', ('java',)),
    SkillDefinition('JavaScript', 'hard', ('javascript', 'js')),
    SkillDefinition('TypeScript', 'hard', ('typescript', 'ts')),
    SkillDefinition('C++', 'hard', ('c++', 'cpp')),
    SkillDefinition('C#', 'hard', ('c#', 'csharp', '.net', 'dotnet')),
    SkillDefinition('Ruby', 'hard', ('ruby',)),
    SkillDefinition('Go', 'hard', ('go', 'golang')),
    SkillDefinition('Rust', 'hard', ('rust',)),
    SkillDefinition('Kotlin', 'hard', ('kotlin',)),
    SkillDefinition('Swift', 'hard', ('swift',)),
    SkillDefinition('PHP', 'hard', ('php',)),
    SkillDefinition('HTML', 'hard', ('html',)),
    SkillDefinition('CSS', 'hard', ('css',)),
    SkillDefinition('React', 'hard', ('react', 'react.js', 'reactjs')),
    SkillDefinition('Angular', 'hard', ('angular', 'angularjs')),
    SkillDefinition('Vue', 'hard', ('vue', 'vue.js', 'vuejs')),
    SkillDefinition('Node.js', 'hard', ('node.js', 'nodejs', 'node')),
    SkillDefinition('Express', 'hard', ('express', 'express.js', 'expressjs')),
    SkillDefinition('Flutter', 'hard', ('flutter',)),
    SkillDefinition('Django', 'hard', ('django',)),
    SkillDefinition('Flask', 'hard', ('flask',)),
    SkillDefinition('FastAPI', 'hard', ('fastapi', 'fast api')),
    SkillDefinition('GraphQL', 'hard', ('graphql', 'graph ql')),
    SkillDefinition('REST API', 'hard', ('rest api', 'restful api', 'rest api development', 'rest services')),
    SkillDefinition('Microservices', 'hard', ('microservices', 'micro services')),
    SkillDefinition('SQL', 'hard', ('sql', 'structured query language')),
    SkillDefinition('MongoDB', 'hard', ('mongodb', 'mongo db', 'mongo')),
    SkillDefinition('PostgreSQL', 'hard', ('postgresql', 'postgres', 'psql')),
    SkillDefinition('MySQL', 'hard', ('mysql', 'my sql')),
    SkillDefinition('Redis', 'hard', ('redis',)),
    SkillDefinition('Elasticsearch', 'hard', ('elasticsearch', 'elastic search', 'elastic')),
    SkillDefinition('AWS', 'hard', ('aws', 'amazon web services')),
    SkillDefinition('Azure', 'hard', ('azure', 'microsoft azure')),
    SkillDefinition('GCP', 'hard', ('gcp', 'google cloud platform', 'google cloud')),
    SkillDefinition('Docker', 'hard', ('docker', 'containers')),
    SkillDefinition('Kubernetes', 'hard', ('kubernetes', 'k8s')),
    SkillDefinition('Jenkins', 'hard', ('jenkins',)),
    SkillDefinition('Terraform', 'hard', ('terraform',)),
    SkillDefinition('Linux', 'hard', ('linux',)),
    SkillDefinition('Git', 'hard', ('git', 'github', 'gitlab', 'bitbucket')),
    SkillDefinition('Machine Learning', 'hard', ('machine learning', 'ml')),
    SkillDefinition('Deep Learning', 'hard', ('deep learning', 'dl')),
    SkillDefinition('AI', 'hard', ('ai', 'artificial intelligence')),
    SkillDefinition('Data Science', 'hard', ('data science',)),
    SkillDefinition('Data Analysis', 'hard', ('data analysis', 'data analytics')),
    SkillDefinition('Tableau', 'hard', ('tableau',)),
    SkillDefinition('Power BI', 'hard', ('power bi', 'powerbi')),
    SkillDefinition('Excel', 'hard', ('excel', 'microsoft excel')),
    SkillDefinition('Pandas', 'hard', ('pandas',)),
    SkillDefinition('NumPy', 'hard', ('numpy', 'num py')),
    SkillDefinition('TensorFlow', 'hard', ('tensorflow', 'tensor flow')),
    SkillDefinition('PyTorch', 'hard', ('pytorch', 'py torch')),
    SkillDefinition('Agile', 'hard', ('agile',)),
    SkillDefinition('Scrum', 'hard', ('scrum',)),
    SkillDefinition('DevOps', 'hard', ('devops', 'dev ops')),
    SkillDefinition('Programming', 'hard', ('programming', 'coding')),
    SkillDefinition('Software Development', 'hard', ('software development', 'software engineering')),
    SkillDefinition('Information Technology', 'hard', ('information technology', 'it')),
    SkillDefinition('Computer Science', 'hard', ('computer science', 'cs')),
    SkillDefinition('Networking', 'hard', ('networking', 'computer networks')),
    SkillDefinition('Computer Hardware', 'hard', ('computer hardware', 'hardware troubleshooting')),
    SkillDefinition('Database Management', 'hard', ('database management', 'database administration')),
    SkillDefinition('Systems Analysis', 'hard', ('systems analysis', 'system analysis')),
    SkillDefinition('Object-Oriented Programming', 'hard', ('object-oriented programming', 'object oriented programming', 'oop')),
    SkillDefinition('Unit Testing', 'hard', ('unit testing', 'automated testing', 'tests')),
    SkillDefinition('Debugging', 'hard', ('debugging', 'troubleshooting')),
    SkillDefinition('Virtual Environment', 'hard', ('virtual environment', 'virtualenv', 'venv')),
    SkillDefinition('Web Development', 'hard', ('web development',)),
    SkillDefinition('Backend Development', 'hard', ('backend development', 'back end development', 'backend engineering')),
    SkillDefinition('Full Stack Development', 'hard', ('full stack development', 'fullstack development', 'full-stack development')),
    SkillDefinition('Cybersecurity', 'hard', ('cybersecurity', 'cyber security')),
    SkillDefinition('Network Security', 'hard', ('network security',)),
    SkillDefinition('Information Security', 'hard', ('information security', 'infosec')),
    SkillDefinition('Ethical Hacking', 'hard', ('ethical hacking',)),
    SkillDefinition('Penetration Testing', 'hard', ('penetration testing', 'pen testing', 'pentesting')),
    SkillDefinition('Cryptography', 'hard', ('cryptography',)),
    SkillDefinition('Security Operations', 'hard', ('security operations', 'soc')),
    SkillDefinition('Vulnerability Assessment', 'hard', ('vulnerability assessment', 'vulnerability management')),
    SkillDefinition('IT Consultancy', 'hard', ('it consultancy', 'it consulting')),
    SkillDefinition('IT Certification', 'hard', ('it certification', 'technical certification')),
    SkillDefinition('Technical Consulting', 'hard', ('technical consulting',)),
    SkillDefinition('Applied Research', 'hard', ('applied research',)),
    SkillDefinition('E-Learning', 'hard', ('e-learning', 'elearning')),
    SkillDefinition('LMS', 'hard', ('lms', 'learning management system', 'learning management systems')),
    SkillDefinition('Educational Technology', 'hard', ('educational technology', 'edtech', 'ed tech')),
    SkillDefinition('AI Integration', 'hard', ('ai integration', 'artificial intelligence integration')),
    SkillDefinition('Digital Transformation', 'hard', ('digital transformation',)),
    SkillDefinition('Blended Learning', 'hard', ('blended learning',)),
    SkillDefinition('Technology-Enhanced Learning', 'hard', ('technology-enhanced learning', 'technology enhanced learning')),
    SkillDefinition('Communication', 'soft', ('communication', 'communications')),
    SkillDefinition('Teamwork', 'soft', ('teamwork', 'team work')),
    SkillDefinition('Leadership', 'soft', ('leadership', 'team leadership')),
    SkillDefinition('Problem Solving', 'soft', ('problem solving', 'problem-solving')),
    SkillDefinition('Critical Thinking', 'soft', ('critical thinking',)),
    SkillDefinition('Time Management', 'soft', ('time management',)),
    SkillDefinition('Adaptability', 'soft', ('adaptability', 'adaptable')),
    SkillDefinition('Creativity', 'soft', ('creativity', 'creative thinking')),
    SkillDefinition('Collaboration', 'soft', ('collaboration', 'collaborative')),
    SkillDefinition('Decision Making', 'soft', ('decision making', 'decision-making')),
    SkillDefinition('Teaching', 'soft', ('teaching', 'instruction')),
    SkillDefinition('Curriculum Development', 'soft', ('curriculum development', 'curriculum design')),
    SkillDefinition('Course Design', 'soft', ('course design', 'course development')),
    SkillDefinition('Research', 'soft', ('research', 'research writing')),
    SkillDefinition('Lecturing', 'soft', ('lecturing', 'lecture delivery')),
    SkillDefinition('Academic Writing', 'soft', ('academic writing',)),
    SkillDefinition('Mentoring', 'soft', ('mentoring', 'mentorship')),
    SkillDefinition('Laboratory Supervision', 'soft', ('laboratory supervision', 'lab supervision')),
    SkillDefinition('Thesis Supervision', 'soft', ('thesis supervision',)),
    SkillDefinition('Lesson Planning', 'soft', ('lesson planning',)),
    SkillDefinition('Classroom Management', 'soft', ('classroom management',)),
    SkillDefinition('Student Engagement', 'soft', ('student engagement',)),
    SkillDefinition('Instructional Design', 'soft', ('instructional design',)),
    SkillDefinition('Course Facilitation', 'soft', ('course facilitation',)),
    SkillDefinition('Learning Management', 'soft', ('learning management',)),
    SkillDefinition('Student Discipline', 'soft', ('student discipline',)),
    SkillDefinition('Student Assessment', 'soft', ('student assessment', 'assessment design')),
    SkillDefinition('Student Evaluation', 'soft', ('student evaluation',)),
    SkillDefinition('Academic Advising', 'soft', ('academic advising', 'student advising')),
    SkillDefinition('Career Guidance', 'soft', ('career guidance', 'career counseling')),
    SkillDefinition('Seminar Facilitation', 'soft', ('seminar facilitation',)),
    SkillDefinition('Laboratory Instruction', 'soft', ('laboratory instruction', 'lab instruction')),
    SkillDefinition('Professional Development', 'soft', ('professional development',)),
    SkillDefinition('Faculty Development', 'soft', ('faculty development',)),
    SkillDefinition('Online Learning', 'soft', ('online learning', 'distance learning')),
    SkillDefinition('Online Course Development', 'soft', ('online course development',)),
    SkillDefinition('Interpersonal Skills', 'soft', ('interpersonal skills', 'interpersonal skill')),
    SkillDefinition('Coaching', 'soft', ('coaching',)),
    SkillDefinition('Constructive Feedback', 'soft', ('constructive feedback',)),
    SkillDefinition('Presentation Skills', 'soft', ('presentation skills', 'presentation skill')),
    SkillDefinition('Project-Based Learning', 'soft', ('project-based learning', 'project based learning')),
    SkillDefinition('Student Reporting', 'soft', ('student reporting',)),
    SkillDefinition('Guest Service', 'soft', ('guest service', 'guest services', 'guest satisfaction', 'guest inquiries', 'vip guests')),
    SkillDefinition('Customer Service', 'soft', ('customer service', 'customer support', 'client service')),
    SkillDefinition('Order Taking', 'soft', ('order taking', 'take orders', 'taking orders')),
    SkillDefinition('Food Service', 'hard', ('food service', 'food items', 'buffet service', 'buffet areas')),
    SkillDefinition('Beverage Service', 'hard', ('beverage service', 'beverage items', 'beverages', 'drink service')),
    SkillDefinition('Inventory Management', 'hard', ('inventory management', 'stock condiments', 'stock shelves', 'procurement')),
    SkillDefinition('Sanitation', 'hard', ('sanitation', 'sanitized', 'clean and sanitized', 'clean and organized')),
)


def _normalize_text(value: str) -> str:
    return ' '.join((value or '').strip().lower().split())


def _build_alias_pattern(alias: str) -> re.Pattern[str]:
    escaped = re.escape(alias)
    return re.compile(rf'(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])', re.IGNORECASE)


_SKILL_PATTERNS: tuple[tuple[SkillDefinition, tuple[re.Pattern[str], ...]], ...] = tuple(
    (definition, tuple(_build_alias_pattern(alias) for alias in definition.aliases))
    for definition in _SKILL_DEFINITIONS
)

_CANONICAL_BY_ALIAS = {
    _normalize_text(alias): definition.canonical_name
    for definition in _SKILL_DEFINITIONS
    for alias in definition.aliases
}

_CATEGORY_BY_CANONICAL = {
    definition.canonical_name: definition.category
    for definition in _SKILL_DEFINITIONS
}

_EXPERIENCE_LEVEL_ALIASES = {
    'entry level': ('entry level', 'entry-level', 'entry', 'junior', 'jr', 'fresher', 'intern', 'trainee', 'associate'),
    'mid level': ('mid level', 'mid-level', 'mid', 'middle', 'intermediate', 'staff'),
    'senior': ('senior', 'senior level', 'senior-level', 'sr'),
    'lead': ('lead', 'team lead', 'technical lead', 'tech lead'),
    'manager': ('manager', 'supervisor'),
    'director': ('director', 'head'),
    'principal': ('principal', 'architect'),
    'vp': ('vp', 'vice president'),
    'c-level': ('c-level', 'c suite', 'executive', 'cto', 'cio', 'ceo'),
}

_EMPLOYMENT_TYPE_ALIASES = {
    'Full-Time / Part-Time': (
        'full-time or part-time',
        'full time or part time',
        'full-time/part-time',
        'full time/part time',
        'full-time and part-time',
        'full time and part time',
        'part-time or full-time',
        'part time or full time',
        'part-time/full-time',
        'part time/full time',
        'part-time and full-time',
        'part time and full time',
        'full-time / part-time',
        'full time / part time',
        'part-time / full-time',
        'part time / full time',
    ),
    'Full-Time': ('full-time', 'full time'),
    'Part-Time': ('part-time', 'part time'),
    'Contract': ('contract', 'contractor', 'contractual'),
    'Temporary': ('temporary', 'temp'),
    'Internship': ('internship', 'intern'),
    'Hybrid': ('hybrid',),
    'Remote': ('remote', 'work from home', 'wfh'),
    'Onsite': ('onsite', 'on-site', 'on site'),
    'Freelance': ('freelance',),
    'Permanent': ('permanent',),
    'Seasonal': ('seasonal',),
    'Volunteer': ('volunteer',),
    'Commission': ('commission', 'commission-based', 'commission based'),
}


def get_skill_definitions() -> tuple[SkillDefinition, ...]:
    return _SKILL_DEFINITIONS


def get_canonical_skills(category: Optional[str] = None) -> list[str]:
    return [
        definition.canonical_name
        for definition in _SKILL_DEFINITIONS
        if category is None or definition.category == category
    ]


def canonicalize_skill_name(value: str) -> Optional[str]:
    normalized = _normalize_text(value)
    return _CANONICAL_BY_ALIAS.get(normalized)


def canonicalize_skill_list(values: Iterable[str]) -> list[str]:
    canonical = []
    seen = set()
    for value in values or []:
        resolved = canonicalize_skill_name(str(value))
        if resolved is None:
            resolved = normalize_title_text(str(value))
        if resolved and resolved not in seen:
            seen.add(resolved)
            canonical.append(resolved)
    return canonical


def extract_canonical_skills(text: str, category: Optional[str] = None) -> list[str]:
    text = text or ''
    matches = []
    for definition, patterns in _SKILL_PATTERNS:
        if category is not None and definition.category != category:
            continue
        if any(pattern.search(text) for pattern in patterns):
            matches.append(definition.canonical_name)
    return matches


def get_skill_category(skill: str) -> str:
    canonical = canonicalize_skill_name(skill) or normalize_title_text(skill)
    return _CATEGORY_BY_CANONICAL.get(canonical, 'hard')


def split_skills_by_category(skills: Iterable[str]) -> tuple[list[str], list[str]]:
    hard_skills = []
    soft_skills = []
    for skill in canonicalize_skill_list(skills):
        if get_skill_category(skill) == 'soft':
            soft_skills.append(skill)
        else:
            hard_skills.append(skill)
    return hard_skills, soft_skills


def normalize_whitespace(value: Optional[str]) -> str:
    return ' '.join((value or '').split())


def normalize_title_text(value: Optional[str]) -> str:
    acronym_map = {
        'ai': 'AI',
        'api': 'API',
        'aws': 'AWS',
        'css': 'CSS',
        'gcp': 'GCP',
        'html': 'HTML',
        'it': 'IT',
        'lms': 'LMS',
        'ml': 'ML',
        'php': 'PHP',
        'qa': 'QA',
        'sql': 'SQL',
        'ui': 'UI',
        'ux': 'UX',
    }
    tokens = []
    for token in normalize_whitespace(value).split():
        normalized = token.strip()
        if not normalized:
            continue
        lookup = normalized.lower().strip('.,()')
        if lookup in acronym_map:
            replacement = acronym_map[lookup]
            if normalized[-1:] in '.,)' and normalized[-1:] not in replacement:
                replacement = f'{replacement}{normalized[-1]}'
            tokens.append(replacement)
            continue
        tokens.append(normalized.title())
    return ' '.join(tokens)


def normalize_list_text(values: Iterable[str]) -> list[str]:
    normalized = []
    seen = set()
    for value in values or []:
        cleaned = normalize_whitespace(str(value))
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            normalized.append(cleaned)
    return normalized


def normalize_location_text(value: Optional[str]) -> str:
    cleaned = normalize_whitespace(value)
    cleaned = re.sub(r'\s*,\s*', ', ', cleaned)
    return cleaned.strip(' ,')


def normalize_experience_level(value: Optional[str]) -> Optional[str]:
    normalized = _normalize_text(value or '')
    if not normalized:
        return None
    for canonical, aliases in _EXPERIENCE_LEVEL_ALIASES.items():
        if normalized in aliases:
            return canonical
    return normalized


def normalize_employment_type(value: Optional[str]) -> Optional[str]:
    normalized = _normalize_text(value or '')
    if not normalized:
        return None
    for canonical, aliases in _EMPLOYMENT_TYPE_ALIASES.items():
        if normalized in aliases:
            return canonical
    return normalize_title_text(normalized)


def normalize_education_entries(values: Iterable[str]) -> list[str]:
    return normalize_list_text(values)
