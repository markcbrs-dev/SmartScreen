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

    def test_matcher_ignores_low_confidence_title_prediction_scores(self):
        resume = ResumeProfile(
            raw_text='Python TensorFlow machine learning',
            skills=['Python', 'TensorFlow'],
            education=['Bachelor of Science'],
            experience=['Machine learning engineer'],
        )
        job = JobProfile(
            title='AI Engineer',
            raw_text='Python TensorFlow machine learning',
            required_skills=['Python', 'TensorFlow'],
        )

        class StubClassifier:
            def score_label(self, text, label):
                return 0.3

        with patch('main.services.matcher.get_job_title_classifier', return_value=StubClassifier()):
            result = ResumeJobMatcher().match_resume_to_job(resume, job)

        self.assertNotIn('title_prediction_score', result.breakdown)

    def test_matcher_uses_structured_entities_for_keyword_similarity_when_raw_text_is_sparse(self):
        resume = ResumeProfile(
            raw_text='miscellaneous text',
            skills=['Python', 'TensorFlow'],
            education=['Bachelor of Science'],
            experience=['Built machine learning services'],
        )
        job = JobProfile(
            title='AI Engineer',
            raw_text='unrelated posting copy',
            required_skills=['Python', 'TensorFlow'],
            preferred_skills=['PyTorch'],
            education_requirements=['Bachelor of Science'],
        )

        with patch.object(ResumeJobMatcher, 'calculate_job_title_prediction_score', return_value=None):
            result = ResumeJobMatcher().match_resume_to_job(resume, job)

        self.assertGreater(result.keyword_match_score, 0.0)
        self.assertIn('Python', result.matching_skills)

    def test_matcher_caps_overall_score_when_resume_lacks_most_required_skills(self):
        resume = ResumeProfile(
            raw_text='Senior developer with Python, leadership, architecture, and stakeholder management.',
            skills=['Python', 'Leadership', 'Communication'],
            education=['Bachelor of Science in Computer Science'],
            experience=['Senior software engineer with 8 years of experience'],
        )
        job = JobProfile(
            title='Backend Platform Engineer',
            raw_text='Python Django REST API PostgreSQL Docker Kubernetes backend platform engineering',
            required_skills=['Python', 'Django', 'REST API', 'PostgreSQL', 'Docker'],
            preferred_skills=['Kubernetes'],
            experience_level='senior',
            education_requirements=['Bachelor of Science in Computer Science'],
        )

        with patch.object(ResumeJobMatcher, 'calculate_job_title_prediction_score', return_value=None):
            result = ResumeJobMatcher().match_resume_to_job(resume, job)

        self.assertLessEqual(result.skill_match_score, 0.3)
        self.assertLessEqual(result.overall_score, 0.3)

    def test_matcher_treats_missing_job_skills_as_neutral_not_perfect(self):
        resume = ResumeProfile(
            raw_text='Generalist engineer with strong communication and leadership.',
            skills=['Communication', 'Leadership'],
            education=[],
            experience=[],
        )
        job = JobProfile(
            title='Operations Generalist',
            raw_text='Join our growing team and support daily operations.',
            required_skills=[],
            preferred_skills=[],
        )

        with patch.object(ResumeJobMatcher, 'calculate_job_title_prediction_score', return_value=None):
            result = ResumeJobMatcher().match_resume_to_job(resume, job)

        self.assertEqual(result.skill_match_score, 0.5)
        self.assertLess(result.overall_score, 1.0)

    def test_matcher_weights_hard_skills_above_soft_skills(self):
        resume = ResumeProfile(
            raw_text='Strong communicator and team leader with stakeholder management experience.',
            skills=['Communication', 'Leadership'],
            education=['Bachelor of Science in Computer Science'],
            experience=['Team lead with 5 years of experience'],
        )
        job = JobProfile(
            title='Backend Engineer',
            raw_text='Python communication backend engineer',
            required_skills=['Python', 'Communication'],
            preferred_skills=[],
            education_requirements=['Bachelor of Science in Computer Science'],
        )

        with patch.object(ResumeJobMatcher, 'calculate_job_title_prediction_score', return_value=None):
            result = ResumeJobMatcher().match_resume_to_job(resume, job)

        self.assertLess(result.skill_match_score, 0.5)
        self.assertIn('Python', result.missing_skills)

    def test_estimate_experience_level_uses_explicit_years(self):
        matcher = ResumeJobMatcher()

        self.assertEqual(matcher._estimate_experience_level(['Worked 1 year in support roles']), 1)
        self.assertEqual(matcher._estimate_experience_level(['Accumulated 3 years of backend engineering']), 2)
        self.assertEqual(matcher._estimate_experience_level(['Built systems for 5 years']), 3)
        self.assertEqual(matcher._estimate_experience_level(['Led teams for 8 years']), 4)
        self.assertEqual(matcher._estimate_experience_level(['Directed programs for 10 years']), 5)