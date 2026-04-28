import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from main.services.entities import JobProfile, ResumeProfile
from main.services.matcher import ResumeJobMatcher
from main.services.spacy_training import (
    load_job_augmentation_examples,
    load_resume_examples,
    train_textcat_model,
)


class SpacyTrainingTests(SimpleTestCase):
    def test_load_resume_examples_filters_low_match_scores(self):
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / 'resume.csv'
            with csv_path.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=['job_position_name', 'career_objective', 'skills', 'matched_score'])
                writer.writeheader()
                writer.writerow({
                    'job_position_name': 'AI Engineer',
                    'career_objective': 'Build AI systems',
                    'skills': "['Python', 'PyTorch']",
                    'matched_score': '0.90',
                })
                writer.writerow({
                    'job_position_name': 'DevOps Engineer',
                    'career_objective': 'Operate infrastructure',
                    'skills': "['Docker', 'Kubernetes']",
                    'matched_score': '0.20',
                })

            examples, labels = load_resume_examples(csv_path, min_match_score=0.6)

            self.assertEqual(len(examples), 1)
            self.assertEqual(labels, ['AI Engineer'])
            self.assertIn('Career objective: Build AI systems', examples[0][0])

    def test_load_job_augmentation_examples_maps_known_ai_label(self):
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / 'jobs.csv'
            json_path = Path(temp_dir) / 'jobs.json'

            with csv_path.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=['Title', 'ExperienceLevel', 'Skills', 'Responsibilities', 'Keywords'])
                writer.writeheader()
                writer.writerow({
                    'Title': 'AI Engineer - Fresher',
                    'ExperienceLevel': 'Entry-Level',
                    'Skills': 'Python; TensorFlow',
                    'Responsibilities': 'Build models; Clean data',
                    'Keywords': 'AI; ML',
                })

            json_path.write_text(json.dumps([
                {
                    'Title': 'AI Engineer - Experienced',
                    'ExperienceLevel': 'Senior-Level',
                    'Skills': ['Python', 'PyTorch'],
                    'Responsibilities': ['Deploy models'],
                    'Keywords': ['AI', 'MLOps'],
                }
            ]), encoding='utf-8')

            examples = load_job_augmentation_examples(csv_path, json_path, known_labels={'AI Engineer'})

            self.assertEqual(len(examples), 2)
            self.assertTrue(all(label == 'AI Engineer' for _, label in examples))

    def test_train_textcat_model_writes_model_artifacts(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            resume_csv = temp_path / 'resume.csv'
            output_dir = temp_path / 'model'

            with resume_csv.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=['job_position_name', 'career_objective', 'skills', 'positions', 'matched_score'])
                writer.writeheader()
                writer.writerow({
                    'job_position_name': 'AI Engineer',
                    'career_objective': 'Build machine learning systems',
                    'skills': "['Python', 'TensorFlow']",
                    'positions': "['ML Intern']",
                    'matched_score': '0.95',
                })
                writer.writerow({
                    'job_position_name': 'AI Engineer',
                    'career_objective': 'Work on NLP and deep learning',
                    'skills': "['Python', 'PyTorch']",
                    'positions': "['Research Intern']",
                    'matched_score': '0.90',
                })
                writer.writerow({
                    'job_position_name': 'DevOps Engineer',
                    'career_objective': 'Run cloud infrastructure and deployments',
                    'skills': "['Docker', 'Kubernetes']",
                    'positions': "['Platform Engineer']",
                    'matched_score': '0.95',
                })
                writer.writerow({
                    'job_position_name': 'DevOps Engineer',
                    'career_objective': 'Automate CI CD pipelines',
                    'skills': "['Azure DevOps', 'Terraform']",
                    'positions': "['DevOps Intern']",
                    'matched_score': '0.92',
                })

            metadata = train_textcat_model(
                resume_csv_path=resume_csv,
                output_dir=output_dir,
                epochs=1,
                train_fraction=0.75,
                min_match_score=0.5,
                seed=7,
            )

            self.assertTrue((output_dir / 'config.cfg').exists())
            self.assertTrue((output_dir / 'training_meta.json').exists())
            self.assertEqual(sorted(metadata['labels']), ['AI Engineer', 'DevOps Engineer'])
            self.assertGreaterEqual(metadata['total_examples'], 4)


class MatcherClassifierHookTests(SimpleTestCase):
    def test_matcher_adds_title_prediction_score_when_classifier_available(self):
        resume = ResumeProfile(
            raw_text='Python TensorFlow PyTorch machine learning',
            skills=['Python', 'TensorFlow'],
            education=['Bachelor of Science'],
            experience=['Machine learning engineer'],
        )
        job = JobProfile(
            title='AI Engineer',
            raw_text='Python TensorFlow PyTorch machine learning',
            required_skills=['Python', 'TensorFlow'],
            preferred_skills=['PyTorch'],
            education_requirements=['Bachelor of Science'],
        )

        class StubClassifier:
            def score_label(self, text, label):
                return 0.8

        with patch('main.services.matcher.get_job_title_classifier', return_value=StubClassifier()):
            result = ResumeJobMatcher().match_resume_to_job(resume, job)

        self.assertIn('title_prediction_score', result.breakdown)
        self.assertAlmostEqual(result.breakdown['title_prediction_score'], 0.8)