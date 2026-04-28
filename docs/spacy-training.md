# spaCy Training

This project now includes a spaCy text-classification pipeline for resume-to-job-title prediction.

## What Gets Trained

The trained component is a `textcat` model.

- Input text: a synthesized resume profile built from columns such as career objective, skills, degrees, prior positions, responsibilities, languages, and certification skills.
- Target label: the job title stored in `resume_data.csv` under `job_position_name`.
- Optional augmentation: matching entries from `job_dataset.csv` and `job_dataset.json` are converted into synthetic job-title examples when their titles can be normalized to an existing resume label.

This is different from the built-in `en_core_web_sm` model used by the parsers. That model is pre-trained by spaCy and supports entity extraction. The project-specific classifier is trained from your datasets and predicts likely job titles.

## Training Command

Run the trainer from the Django project root:

```powershell
.venv\Scripts\python.exe manage.py train_spacy_model
```

Useful options:

```powershell
.venv\Scripts\python.exe manage.py train_spacy_model --epochs 12 --min-match-score 0.6 --output-dir models\spacy_job_classifier
```

If you want a faster experimental run before a full training pass:

```powershell
.venv\Scripts\python.exe manage.py train_spacy_model --epochs 3 --max-examples 2500
```

Generated files:

- `models/spacy_job_classifier/` - the saved spaCy pipeline
- `models/spacy_job_classifier/training_meta.json` - labels, data counts, metrics, and training settings

## Training Flow

1. Load `resume_data.csv` and keep rows with a usable job label and `matched_score >= min_match_score`.
2. Build a clean text sample from the structured resume columns.
3. Load `job_dataset.csv` and `job_dataset.json` and add any rows whose titles can be normalized to one of the known resume labels.
4. Deduplicate the examples.
5. Split the dataset into train/dev sets.
6. Train a blank English spaCy pipeline with the `textcat` component.
7. Evaluate top-1 and top-3 accuracy on the held-out dev set.
8. Save the trained pipeline and metadata.

## How The App Uses It

If a trained model exists in `models/spacy_job_classifier/`, the matcher adds a `title_prediction_score` when the job title matches one of the trained labels. If no trained model exists, matching falls back to the previous heuristic-only behavior.

## spaCy Package vs spaCy Model

The repository now depends on the `spacy` Python package. If you also want the existing resume/job parsers to use spaCy's pre-trained NER features, install the English model separately:

```powershell
.venv\Scripts\python.exe -m spacy download en_core_web_sm
```

Then enable it in your environment:

```powershell
$env:SMARTSCREEN_ENABLE_SPACY="1"
```