"""
main/services/spacy_training.py — Training data loading and spaCy textcat model training.

OVERVIEW
--------
This module is used exclusively by the `train_spacy_model` management command.
It reads labeled training data from CSV/JSON files, trains a spaCy textcat
model, and writes the artifact to disk.

TRAINING DATA FILES (in SmartScreen/ root)
------------------------------------------
  resume_data.csv  — labeled resume rows; column `job_position_name` is the label.
                      Only rows with `matched_score >= min_match_score` are used.
  job_dataset.csv  — augmentation data; provides additional examples per job title.
  job_dataset.json — augmentation data in JSON format.

HOW TO MODIFY
-------------
  Add a new job label       → add rows with that label in resume_data.csv or job_dataset files.
  Add a label alias         → add an entry to LABEL_ALIASES (e.g. variants of the same role).
  Change training parameters→ pass different arguments to train_textcat_model(); defaults
                               are set in train_spacy_model.py (the management command).
  Change text columns used  → edit RESUME_TEXT_COLUMNS or JOB_TEXT_COLUMNS tuples.
  Change min score filter   → pass a different min_match_score to train_textcat_model();
                               default is 0.6 (set in the management command).
  Change training epochs    → --epochs argument on manage.py train_spacy_model (default 12).

DEBUG
-----
  Low accuracy?             → check label distribution in training data; add more examples
                               or tune --epochs / --dropout / --min-match-score.
  KeyError on label?        → check LABEL_ALIASES for unmapped variants.
  Output model not loading? → ensure output_dir is correct and training completed without error.
"""

import ast
import csv
import json
import random
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

try:
    import spacy
    from spacy.training import Example
    from spacy.util import compounding, minibatch
except ImportError:
    spacy = None
    Example = None
    compounding = None
    minibatch = None


NULL_TOKENS = {'', 'n/a', 'na', 'none', 'null', 'nan'}
RESUME_LABEL_COLUMNS = ('job_position_name', '\ufeffjob_position_name')
RESUME_TEXT_COLUMNS = (
    ('career_objective', 'Career objective'),
    ('skills', 'Skills'),
    ('degree_names', 'Degrees'),
    ('major_field_of_studies', 'Fields of study'),
    ('positions', 'Past positions'),
    ('responsibilities', 'Responsibilities'),
    ('languages', 'Languages'),
    ('certification_skills', 'Certifications'),
)
JOB_TEXT_COLUMNS = (
    ('Title', 'Job title'),
    ('ExperienceLevel', 'Experience level'),
    ('YearsOfExperience', 'Years of experience'),
    ('Skills', 'Skills'),
    ('Responsibilities', 'Responsibilities'),
    ('Keywords', 'Keywords'),
)
LABEL_ALIASES = {
    'ai engineer - fresher': 'AI Engineer',
    'ai engineer - experienced': 'AI Engineer',
    'entry-level ai engineer': 'AI Engineer',
    'mid-senior level ai engineer': 'AI Engineer',
    'senior-level ai engineer': 'AI Engineer',
}
MAX_TRAINING_TEXT_LENGTH = 2500


def normalize_job_label(label: str, known_labels: set[str] | None = None) -> str:
    candidate = normalize_text(label)
    if not candidate:
        return ''

    if known_labels:
        exact_match = next(
            (known for known in known_labels if known.casefold() == candidate.casefold()),
            None,
        )
        if exact_match is not None:
            return exact_match

    lower_candidate = candidate.casefold().replace('–', '-').replace('—', '-')
    lower_candidate = re.sub(r'\s+', ' ', lower_candidate)
    alias = LABEL_ALIASES.get(lower_candidate)
    if alias:
        return alias

    if lower_candidate.startswith('ai engineer'):
        return 'AI Engineer'

    return candidate


def normalize_text(value) -> str:
    text = str(value or '').replace('\ufeff', ' ')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def parse_collection(value) -> list[str]:
    text = str(value or '').strip()
    if not text:
        return []

    lowered = text.casefold()
    if lowered in NULL_TOKENS:
        return []

    if text[:1] in {'[', '('}:
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            parsed = None
        if parsed is not None:
            return flatten_collection(parsed)

    if ';' in text:
        return [item for item in (normalize_text(part) for part in text.split(';')) if item]

    if '\n' in text:
        return [item for item in (normalize_text(part) for part in text.splitlines()) if item]

    normalized = normalize_text(text)
    return [normalized] if normalized and normalized.casefold() not in NULL_TOKENS else []


def flatten_collection(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        flattened = []
        for item in value:
            flattened.extend(flatten_collection(item))
        return flattened

    normalized = normalize_text(value)
    if not normalized or normalized.casefold() in NULL_TOKENS:
        return []
    return [normalized]


def load_resume_examples(resume_csv_path: str | Path, min_match_score: float = 0.6) -> tuple[list[tuple[str, str]], list[str]]:
    rows = read_csv_rows(resume_csv_path)
    examples = []
    labels = set()

    for row in rows:
        label = ''
        for column in RESUME_LABEL_COLUMNS:
            label = normalize_job_label(row.get(column, ''))
            if label:
                break
        if not label:
            continue

        if parse_float(row.get('matched_score', '0')) < min_match_score:
            continue

        text = build_resume_training_text(row)
        if not text:
            continue

        examples.append((text, label))
        labels.add(label)

    return deduplicate_examples(examples), sorted(labels)


def load_job_augmentation_examples(
    job_csv_path: str | Path | None = None,
    job_json_path: str | Path | None = None,
    known_labels: set[str] | None = None,
) -> list[tuple[str, str]]:
    examples = []

    if job_csv_path and Path(job_csv_path).exists():
        for row in read_csv_rows(job_csv_path):
            label = normalize_job_label(row.get('Title', ''), known_labels=known_labels)
            if not label or known_labels and label not in known_labels:
                continue
            text = build_job_training_text(row)
            if text:
                examples.append((text, label))

    if job_json_path and Path(job_json_path).exists():
        for row in read_json_rows(job_json_path):
            label = normalize_job_label(row.get('Title', ''), known_labels=known_labels)
            if not label or known_labels and label not in known_labels:
                continue
            text = build_job_training_text(row)
            if text:
                examples.append((text, label))

    return deduplicate_examples(examples)


def build_resume_training_text(row: dict[str, str]) -> str:
    sections = []

    for column, heading in RESUME_TEXT_COLUMNS:
        values = parse_collection(row.get(column, ''))
        if values:
            sections.append(f'{heading}: {", ".join(values)}')

    return truncate_training_text('\n'.join(sections))


def build_job_training_text(row: dict[str, object]) -> str:
    sections = []

    for column, heading in JOB_TEXT_COLUMNS:
        values = parse_collection(row.get(column, ''))
        if values:
            sections.append(f'{heading}: {", ".join(values)}')

    return truncate_training_text('\n'.join(sections))


def truncate_training_text(text: str, max_length: int = MAX_TRAINING_TEXT_LENGTH) -> str:
    normalized = normalize_text(text)
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3].rstrip() + '...'


def read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def read_json_rows(path: str | Path) -> list[dict[str, object]]:
    with Path(path).open(encoding='utf-8') as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError('Job JSON payload must be a list of objects.')
    return [item for item in payload if isinstance(item, dict)]


def deduplicate_examples(examples: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen = set()
    deduplicated = []
    for example in examples:
        if example in seen:
            continue
        seen.add(example)
        deduplicated.append(example)
    return deduplicated


def split_examples(
    examples: list[tuple[str, str]],
    train_fraction: float = 0.85,
    seed: int = 42,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    if not examples:
        return [], []

    randomized = list(examples)
    random.Random(seed).shuffle(randomized)
    split_index = int(len(randomized) * train_fraction)
    split_index = min(max(split_index, 1), len(randomized) - 1) if len(randomized) > 1 else len(randomized)
    return randomized[:split_index], randomized[split_index:]


def parse_float(value) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def make_spacy_examples(nlp, rows: list[tuple[str, str]], labels: list[str]) -> list[Example]:
    return [
        Example.from_dict(
            nlp.make_doc(text),
            {'cats': {label: label == gold_label for label in labels}},
        )
        for text, gold_label in rows
    ]


def evaluate_textcat_model(nlp, rows: list[tuple[str, str]]) -> dict[str, float | int]:
    if not rows:
        return {'accuracy': 1.0, 'top_3_accuracy': 1.0, 'examples': 0}

    correct = 0
    correct_top_3 = 0
    for text, gold_label in rows:
        doc = nlp(text)
        ranked_labels = sorted(doc.cats.items(), key=lambda item: item[1], reverse=True)
        predicted_label = ranked_labels[0][0] if ranked_labels else ''
        if predicted_label == gold_label:
            correct += 1
        top_3_labels = {label for label, _ in ranked_labels[:3]}
        if gold_label in top_3_labels:
            correct_top_3 += 1

    total = len(rows)
    return {
        'accuracy': correct / total,
        'top_3_accuracy': correct_top_3 / total,
        'examples': total,
    }


def train_textcat_model(
    resume_csv_path: str | Path,
    output_dir: str | Path,
    job_csv_path: str | Path | None = None,
    job_json_path: str | Path | None = None,
    min_match_score: float = 0.6,
    train_fraction: float = 0.85,
    epochs: int = 12,
    dropout: float = 0.2,
    seed: int = 42,
    max_examples: int | None = None,
) -> dict[str, object]:
    if spacy is None or Example is None or compounding is None or minibatch is None:
        raise RuntimeError('spaCy is not installed. Install the spacy package before training.')

    resume_examples, labels = load_resume_examples(resume_csv_path, min_match_score=min_match_score)
    if not labels:
        raise ValueError('No labeled resume examples were found after preprocessing.')

    label_set = set(labels)
    job_examples = load_job_augmentation_examples(job_csv_path, job_json_path, known_labels=label_set)
    all_examples = deduplicate_examples([*resume_examples, *job_examples])
    if max_examples is not None and max_examples > 0 and len(all_examples) > max_examples:
        all_examples = random.Random(seed).sample(all_examples, max_examples)
    train_rows, dev_rows = split_examples(all_examples, train_fraction=train_fraction, seed=seed)

    nlp = spacy.blank('en')
    textcat = nlp.add_pipe(
        'textcat',
        config={
            'model': {
                '@architectures': 'spacy.TextCatBOW.v3',
                'exclusive_classes': True,
                'length': 262144,   # larger hash to avoid collisions with bigrams
                'ngram_size': 2,    # unigrams + bigrams; critical for terms like
                                    # "machine learning", "full stack", "data science"
                'no_output_layer': False,
            }
        },
    )
    for label in labels:
        textcat.add_label(label)

    train_examples = make_spacy_examples(nlp, train_rows, labels)
    optimizer = nlp.initialize(get_examples=lambda: train_examples)
    randomizer = random.Random(seed)
    history = []

    for epoch in range(epochs):
        randomizer.shuffle(train_examples)
        losses = {}
        for batch in minibatch(train_examples, size=compounding(4.0, 32.0, 1.5)):
            nlp.update(batch, sgd=optimizer, drop=dropout, losses=losses)

        metrics = evaluate_textcat_model(nlp, dev_rows)
        metrics['epoch'] = epoch + 1
        metrics['loss'] = losses.get('textcat', 0.0)
        history.append(metrics)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    nlp.to_disk(output_path)

    metadata = {
        'trained_at': datetime.now(UTC).isoformat(),
        'labels': labels,
        'resume_examples': len(resume_examples),
        'job_examples': len(job_examples),
        'total_examples': len(all_examples),
        'train_examples': len(train_rows),
        'dev_examples': len(dev_rows),
        'train_fraction': train_fraction,
        'epochs': epochs,
        'dropout': dropout,
        'seed': seed,
        'max_examples': max_examples,
        'min_match_score': min_match_score,
        'label_distribution': dict(sorted(Counter(label for _, label in all_examples).items())),
        'metrics': history[-1] if history else evaluate_textcat_model(nlp, dev_rows),
        'history': history,
        'sources': {
            'resume_csv': str(Path(resume_csv_path)),
            'job_csv': str(Path(job_csv_path)) if job_csv_path else None,
            'job_json': str(Path(job_json_path)) if job_json_path else None,
        },
    }

    with (output_path / 'training_meta.json').open('w', encoding='utf-8') as handle:
        json.dump(metadata, handle, indent=2)

    return metadata