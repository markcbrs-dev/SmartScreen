"""
main/management/commands/train_spacy_model.py — Django management command to train
the spaCy textcat model used for job-title prediction scoring.

USAGE
-----
  python manage.py train_spacy_model
  python manage.py train_spacy_model --epochs 20 --dropout 0.3 --min-match-score 0.7

HOW TO MODIFY
-------------
  Change default arguments  → edit the default= values in add_arguments().
  Change output location    → use --output-dir or change the default path below.
  Change training logic     → edit train_textcat_model() in services/spacy_training.py.
  Change training data      → update resume_data.csv or job_dataset.csv/json.

AFTER TRAINING
--------------
  The model is saved to models/spacy_job_classifier/ (or --output-dir).
  Restart the Django process so the lru_cache in spacy_job_classifier.py reloads it.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from main.services.spacy_training import train_textcat_model


class Command(BaseCommand):
    help = 'Train the spaCy text-classification model used for job-title prediction.'

    def add_arguments(self, parser):
        dataset_dir = Path(settings.BASE_DIR) / 'dataset'
        parser.add_argument(
            '--resume-data',
            default=str(dataset_dir / 'resume_data.csv'),
            help='Path to the labeled resume CSV file.',
        )
        parser.add_argument(
            '--job-data',
            default=str(dataset_dir / 'job_dataset.csv'),
            help='Optional path to the job CSV file used for augmentation.',
        )
        parser.add_argument(
            '--job-json',
            default=str(dataset_dir / 'job_dataset.json'),
            help='Optional path to the job JSON file used for augmentation.',
        )
        parser.add_argument(
            '--seed-job-text',
            action='append',
            default=[str(dataset_dir / 'hospitality_roles.txt')],
            help='Optional raw TXT or DOCX JD file to parse into seeded labels/examples. Can be passed multiple times.',
        )
        parser.add_argument(
            '--output-dir',
            default=str(Path(settings.BASE_DIR) / 'models' / 'spacy_job_classifier'),
            help='Directory where the trained spaCy model will be written.',
        )
        parser.add_argument('--epochs', type=int, default=12, help='Number of training epochs.')
        parser.add_argument('--dropout', type=float, default=0.2, help='Dropout used during training.')
        parser.add_argument('--seed', type=int, default=42, help='Random seed for reproducibility.')
        parser.add_argument(
            '--max-examples',
            type=int,
            default=None,
            help='Optional cap on total training examples after preprocessing and augmentation.',
        )
        parser.add_argument(
            '--train-fraction',
            type=float,
            default=0.85,
            help='Fraction of examples used for training; the rest are held out for validation.',
        )
        parser.add_argument(
            '--min-match-score',
            type=float,
            default=0.6,
            help='Minimum matched_score required for a resume row to be used as a gold label.',
        )

    def handle(self, *args, **options):
        self.stdout.write('Training spaCy text classifier...')
        metadata = train_textcat_model(
            resume_csv_path=options['resume_data'],
            output_dir=options['output_dir'],
            job_csv_path=options.get('job_data') or None,
            job_json_path=options.get('job_json') or None,
            seed_job_text_paths=options.get('seed_job_text') or None,
            min_match_score=options['min_match_score'],
            train_fraction=options['train_fraction'],
            epochs=options['epochs'],
            dropout=options['dropout'],
            seed=options['seed'],
            max_examples=options['max_examples'],
        )

        metrics = metadata['metrics']
        self.stdout.write(self.style.SUCCESS('spaCy model training completed.'))
        self.stdout.write(f"Model directory: {options['output_dir']}")
        self.stdout.write(f"Labels: {len(metadata['labels'])}")
        self.stdout.write(f"Examples: {metadata['total_examples']} total, {metadata['train_examples']} train, {metadata['dev_examples']} dev")
        self.stdout.write(f"Validation accuracy: {metrics['accuracy']:.4f}")
        self.stdout.write(f"Validation top-3 accuracy: {metrics['top_3_accuracy']:.4f}")