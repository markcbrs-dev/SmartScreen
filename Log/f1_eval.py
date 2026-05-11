import json
import os
import sys
from pathlib import Path

base = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(base))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smscrweb.settings')
os.environ.setdefault('SECRET_KEY', 'copilot-f1-key')

import django

django.setup()

import spacy
from sklearn.metrics import accuracy_score, precision_recall_fscore_support
from main.services.spacy_training import (
    deduplicate_examples,
    load_job_augmentation_examples,
    load_resume_examples,
    load_seed_job_text_examples,
    load_seed_resume_text_examples,
    split_examples,
)

meta = json.loads((base / 'models' / 'spacy_job_classifier' / 'training_meta.json').read_text(encoding='utf-8'))
labels = meta['labels']
sources = meta.get('sources', {})

resume_examples, _ = load_resume_examples(
    sources['resume_csv'],
    min_match_score=meta.get('min_match_score', 0.6),
) if sources.get('resume_csv') else ([], [])
seed_job_examples, _ = load_seed_job_text_examples(sources.get('seed_job_texts') or [])
seed_resume_examples, _ = load_seed_resume_text_examples(sources.get('seed_resume_texts') or [])
job_examples = load_job_augmentation_examples(
    sources.get('job_csv'),
    sources.get('job_json'),
    known_labels=set(labels),
)
all_examples = deduplicate_examples([
    *resume_examples,
    *seed_job_examples,
    *seed_resume_examples,
    *job_examples,
])
_, dev_rows = split_examples(
    all_examples,
    train_fraction=meta.get('train_fraction', 0.85),
    seed=meta.get('seed', 42),
)

nlp = spacy.load(base / 'models' / 'spacy_job_classifier')
y_true = []
y_pred = []
for text, gold_label in dev_rows:
    doc = nlp(text)
    ranked = sorted(doc.cats.items(), key=lambda item: item[1], reverse=True)
    y_true.append(gold_label)
    y_pred.append(ranked[0][0] if ranked else '')

macro = precision_recall_fscore_support(y_true, y_pred, labels=labels, average='macro', zero_division=0)
weighted = precision_recall_fscore_support(y_true, y_pred, labels=labels, average='weighted', zero_division=0)
micro = precision_recall_fscore_support(y_true, y_pred, labels=labels, average='micro', zero_division=0)
report = {
    'examples': len(dev_rows),
    'accuracy': accuracy_score(y_true, y_pred),
    'macro_precision': macro[0],
    'macro_recall': macro[1],
    'macro_f1': macro[2],
    'weighted_precision': weighted[0],
    'weighted_recall': weighted[1],
    'weighted_f1': weighted[2],
    'micro_precision': micro[0],
    'micro_recall': micro[1],
    'micro_f1': micro[2],
    'y_true': y_true,
    'y_pred': y_pred,
}
print(json.dumps(report, indent=2))
