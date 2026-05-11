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

The command now defaults to the files under `dataset/` and also seeds labels from `dataset/hospitality_roles.txt` when that file exists.

To add more raw multi-job hospitality files, pass them directly:

```powershell
.venv\Scripts\python.exe manage.py train_spacy_model --seed-job-text dataset\hospitality_roles.txt --seed-job-text dataset\other_non_tech_roles.txt
```

If you want a faster experimental run before a full training pass:

```powershell
.venv\Scripts\python.exe manage.py train_spacy_model --epochs 3 --max-examples 2500
```

Generated files:

- `models/spacy_job_classifier/` - the saved spaCy pipeline
- `models/spacy_job_classifier/training_meta.json` - labels, data counts, metrics, and training settings

## Training Flow

1. Load `dataset/resume_data.csv` and keep rows with a usable job label and `matched_score >= min_match_score`.
2. Build clean text samples from the structured resume columns.
3. Parse any seeded raw JD files, such as `dataset/hospitality_roles.txt`, into one or more job profiles and add their titles as labels.
4. Load `dataset/job_dataset.csv` and `dataset/job_dataset.json` and add any rows whose titles can be normalized to one of the discovered labels.
5. Deduplicate the examples.
6. Split the dataset into train/dev sets.
7. Train a blank English spaCy pipeline with the `textcat` component.
8. Evaluate top-1 and top-3 accuracy on the held-out dev set.
9. Save the trained pipeline and metadata.

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