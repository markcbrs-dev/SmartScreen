"""
main/services/spacy_job_classifier.py — Loads and queries the trained spaCy
textcat model for job-title prediction scores.

OVERVIEW
--------
JobTitleClassifier wraps the trained spaCy textcat model stored in
`models/spacy_job_classifier/` (or the path in SMARTSCREEN_TEXTCAT_MODEL_DIR).
The singleton `get_job_title_classifier()` is called by matcher.py to get
a confidence score for the resume's predicted job title.

HOW TO MODIFY
-------------
  Change model directory    → set env var SMARTSCREEN_TEXTCAT_MODEL_DIR to the new path,
                               or change DEFAULT_MODEL_SUBDIR below.
  Retrain the model         → run: manage.py train_spacy_model
                               (see main/management/commands/train_spacy_model.py)
  Add a label alias         → edit LABEL_ALIASES in spacy_training.py (normalize_job_label)
  Change how score_label works → edit score_label() — it normalises the label via
                               normalize_job_label() before lookup.
  Force a model reload      → call get_job_title_classifier.cache_clear() then
                               get_job_title_classifier() again.

DEBUG
-----
  Model not loading?        → check that `models/spacy_job_classifier/` exists and contains
                               a valid spaCy model (run train_spacy_model first).
  All scores return None?   → check that spacy is installed and model_dir exists.
  Label not recognized?     → inspect get_labels(); check LABEL_ALIASES in spacy_training.py.
  Classifier not being used?→ check that calculate_job_title_prediction_score() in matcher.py
                               returns a non-None value (requires non-empty resume_text + title).
"""

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

try:
    import spacy
except ImportError:
    spacy = None

try:
    from django.conf import settings
except ImportError:
    settings = None

from .spacy_training import normalize_job_label

logger = logging.getLogger(__name__)

MODEL_DIR_ENV = 'SMARTSCREEN_TEXTCAT_MODEL_DIR'
DEFAULT_MODEL_SUBDIR = Path('models') / 'spacy_job_classifier'


class JobTitleClassifier:
    def __init__(self, model_dir: str | Path | None = None):
        self.model_dir = Path(model_dir) if model_dir else self._default_model_dir()
        self._nlp = None
        self._labels = set()

    def _default_model_dir(self) -> Path:
        env_value = os.getenv(MODEL_DIR_ENV, '').strip()
        if env_value:
            return Path(env_value)
        if settings is not None and getattr(settings, 'configured', False):
            return Path(settings.BASE_DIR) / DEFAULT_MODEL_SUBDIR
        return Path.cwd() / DEFAULT_MODEL_SUBDIR

    def load(self):
        if self._nlp is not None:
            return self._nlp
        if spacy is None or not self.model_dir.exists():
            return None
        try:
            self._nlp = spacy.load(self.model_dir)
        except Exception:
            logger.exception('Failed to load spaCy text classifier from %s', self.model_dir)
            return None
        self._labels = self._load_labels()
        return self._nlp

    def _load_labels(self) -> set[str]:
        meta_path = self.model_dir / 'training_meta.json'
        if meta_path.exists():
            try:
                with meta_path.open(encoding='utf-8') as handle:
                    payload = json.load(handle)
                return set(payload.get('labels', []))
            except (OSError, ValueError, TypeError):
                logger.exception('Failed to read classifier metadata from %s', meta_path)
        if self._nlp is None:
            return set()
        return set(self._nlp.pipe_labels.get('textcat', []))

    def get_labels(self) -> set[str]:
        if self._labels:
            return self._labels
        self.load()
        return self._labels

    def predict_scores(self, text: str) -> dict[str, float]:
        normalized_text = ' '.join(text.split())
        if not normalized_text:
            return {}
        nlp = self.load()
        if nlp is None:
            return {}
        doc = nlp(normalized_text)
        return dict(sorted(doc.cats.items(), key=lambda item: item[1], reverse=True))

    def score_label(self, text: str, label: str) -> float | None:
        normalized_label = normalize_job_label(label, known_labels=self.get_labels())
        if not normalized_label:
            return None
        return self.predict_scores(text).get(normalized_label)


@lru_cache(maxsize=1)
def get_job_title_classifier() -> JobTitleClassifier:
    return JobTitleClassifier()