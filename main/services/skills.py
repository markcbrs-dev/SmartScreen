from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Iterable, Optional


@dataclass(frozen=True)
class SkillDefinition:
    canonical_name: str
    category: str
    aliases: tuple[str, ...]


_SKILLS_DATA_PATH = Path(__file__).resolve().parents[2] / 'dataset' / 'skills.json'
_VALID_SKILL_CATEGORIES = frozenset({'hard', 'soft'})


def _normalize_text(value: str) -> str:
    return ' '.join((value or '').strip().lower().split())


def _load_skill_definitions() -> tuple[SkillDefinition, ...]:
    try:
        raw_value = json.loads(_SKILLS_DATA_PATH.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise RuntimeError(f'Skill dictionary file not found: {_SKILLS_DATA_PATH}') from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'Skill dictionary file is not valid JSON: {_SKILLS_DATA_PATH}') from exc

    if not isinstance(raw_value, dict):
        raise RuntimeError('Skill dictionary must be a JSON object keyed by canonical skill name.')

    definitions = []
    seen_names = set()
    for canonical_name, payload in raw_value.items():
        normalized_name = _normalize_text(str(canonical_name))
        if not normalized_name or normalized_name in seen_names:
            raise RuntimeError(f'Duplicate or empty skill name in {_SKILLS_DATA_PATH}: {canonical_name!r}')
        seen_names.add(normalized_name)

        if not isinstance(payload, dict):
            raise RuntimeError(f'Skill entry for {canonical_name!r} must be an object.')

        category = _normalize_text(str(payload.get('category', '')))
        aliases = payload.get('aliases')
        if category not in _VALID_SKILL_CATEGORIES:
            raise RuntimeError(f'Skill entry for {canonical_name!r} has invalid category: {category!r}')
        if not isinstance(aliases, list) or not aliases:
            raise RuntimeError(f'Skill entry for {canonical_name!r} must define a non-empty aliases list.')

        normalized_aliases = []
        seen_aliases = set()
        for alias in aliases:
            alias_text = str(alias).strip()
            normalized_alias = _normalize_text(alias_text)
            if not normalized_alias or normalized_alias in seen_aliases:
                continue
            seen_aliases.add(normalized_alias)
            normalized_aliases.append(alias_text)

        if not normalized_aliases:
            raise RuntimeError(f'Skill entry for {canonical_name!r} has no usable aliases.')

        definitions.append(SkillDefinition(str(canonical_name).strip(), category, tuple(normalized_aliases)))

    return tuple(definitions)


_SKILL_DEFINITIONS = _load_skill_definitions()

_EXCLUDED_NON_SKILLS = frozenset({
    'information technology',
    'computer science',
})


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

_NUMBER_WORD_VALUES = {
    'one': 1,
    'two': 2,
    'three': 3,
    'four': 4,
    'five': 5,
    'six': 6,
    'seven': 7,
    'eight': 8,
    'nine': 9,
    'ten': 10,
    'eleven': 11,
    'twelve': 12,
}

_YEAR_COUNT_PATTERN = re.compile(
    r'\b(?:(?P<word>one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)|(?P<digit>\d+))'
    r'(?:\s*\(\s*(?P<paren>\d+)\s*\))?\s*\+?\s*(?:years?|yrs?)\b',
    re.IGNORECASE,
)

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
    if normalized in _EXCLUDED_NON_SKILLS:
        return None
    return _CANONICAL_BY_ALIAS.get(normalized)


def canonicalize_skill_list(values: Iterable[str]) -> list[str]:
    canonical = []
    seen = set()
    for value in values or []:
        resolved = canonicalize_skill_name(str(value))
        if resolved is None:
            resolved = normalize_title_text(str(value))
        if _normalize_text(resolved or '') in _EXCLUDED_NON_SKILLS:
            continue
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


def extract_years_of_experience_counts(value: Optional[str]) -> list[int]:
    counts = []
    seen = set()
    for match in _YEAR_COUNT_PATTERN.finditer(normalize_whitespace(value)):
        paren_value = match.group('paren')
        digit_value = match.group('digit')
        word_value = match.group('word')
        if paren_value:
            years = int(paren_value)
        elif digit_value:
            years = int(digit_value)
        else:
            years = _NUMBER_WORD_VALUES.get((word_value or '').lower(), 0)
        if years > 0 and years not in seen:
            seen.add(years)
            counts.append(years)
    return counts


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
