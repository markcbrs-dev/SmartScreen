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