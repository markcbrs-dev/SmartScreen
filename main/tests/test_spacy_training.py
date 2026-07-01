import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from main.services.entities import JobProfile, ResumeProfile
from main.services import matcher as matcher_module
from main.services.matcher import ResumeJobMatcher
from main.services.spacy_training import (
    load_seed_job_text_examples,
    load_seed_resume_text_examples,
    load_job_augmentation_examples,
    load_resume_examples,
    normalize_job_label,
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

            self.assertGreaterEqual(len(examples), 2)
            self.assertTrue(all(label == 'AI Engineer' for _, label in examples))

    def test_normalize_job_label_strips_generic_seniority_for_known_non_tech_labels(self):
        label = normalize_job_label(
            'Senior Front Desk Associate',
            known_labels={'Front Desk Associate', 'Recruitment Assistant'},
        )

        self.assertEqual(label, 'Front Desk Associate')

    def test_load_seed_job_text_examples_parses_multi_role_hospitality_text(self):
        with TemporaryDirectory() as temp_dir:
            text_path = Path(temp_dir) / 'hospitality.txt'
            text_path.write_text(
                'Job Title: Barista\n'
                'Company: Keystone Manpower Corporation\n'
                'Duties and Responsibilities:\n'
                '- Prepare beverages following recipes.\n\n'
                'Bartender\n'
                'Company: Keystone Manpower Corporation\n'
                'Duties and Responsibilities:\n'
                '- Mix ingredients to prepare cocktails and other drinks.\n',
                encoding='utf-8',
            )

            examples, labels = load_seed_job_text_examples([text_path])

            self.assertEqual(labels, ['Barista', 'Bartender'])
            self.assertEqual({label for _, label in examples}, {'Barista', 'Bartender'})

    def test_load_seed_job_text_examples_uses_expected_workspace_hospitality_labels(self):
        text_path = Path(__file__).resolve().parents[2] / 'dataset' / 'hospitality_roles.txt'

        examples, labels = load_seed_job_text_examples([text_path])

        self.assertEqual(
            labels,
            [
                'Barista',
                'Bartender',
                'Bellman',
                'Bussing Person',
                'Butler',
                'Gym Attendant',
                'Ingress & Egress Support',
                'Kids Attendant',
                'Kitchen Helper',
            ],
        )
        self.assertEqual({label for _, label in examples}, set(labels))

    def test_load_seed_resume_text_examples_reads_labeled_ocr_payload(self):
        with TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / 'resume-seeds.json'
            json_path.write_text(json.dumps([
                {
                    'label': 'Bellman',
                    'text': (
                        'YRONE BINUYA\n'
                        'Bachelor of Science in Hospitality Management\n'
                        'Bellman / Concierge\n'
                        'Assists guests with luggage, provides information, manages transfers, and arranges accommodations.\n'
                    ),
                },
                {
                    'label': 'Receptionist',
                    'text': (
                        'Meloje Cuizon\n'
                        'Receptionist\n'
                        'APEX MANNING INC.\n'
                        'Communication, teamwork, problem solving.\n'
                    ),
                },
            ]), encoding='utf-8')

            examples, labels = load_seed_resume_text_examples([json_path])

            self.assertEqual(labels, ['Bellman', 'Receptionist'])
            self.assertEqual({label for _, label in examples}, {'Bellman', 'Receptionist'})
            self.assertTrue(any('Assists guests with luggage' in text for text, label in examples if label == 'Bellman'))

    def test_train_textcat_model_accepts_seeded_job_only_labels(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            resume_csv = temp_path / 'resume.csv'
            output_dir = temp_path / 'model'
            seed_text = temp_path / 'hospitality.txt'

            with resume_csv.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=['job_position_name', 'career_objective', 'skills', 'matched_score'])
                writer.writeheader()

            seed_text.write_text(
                'Job Title: Barista\n'
                'Company: Keystone Manpower Corporation\n'
                'Duties and Responsibilities:\n'
                '- Prepare beverages following recipes.\n\n'
                'Job Title: Bartender\n'
                'Company: Keystone Manpower Corporation\n'
                'Duties and Responsibilities:\n'
                '- Mix ingredients to prepare cocktails and other drinks.\n',
                encoding='utf-8',
            )

            metadata = train_textcat_model(
                resume_csv_path=resume_csv,
                output_dir=output_dir,
                seed_job_text_paths=[seed_text],
                epochs=1,
                train_fraction=0.5,
                seed=7,
            )

            self.assertEqual(sorted(metadata['labels']), ['Barista', 'Bartender'])
            self.assertGreaterEqual(metadata['seed_job_examples'], 2)

    def test_train_textcat_model_accepts_seeded_resume_only_labels(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            resume_csv = temp_path / 'resume.csv'
            output_dir = temp_path / 'model'
            seed_resume_text = temp_path / 'resume-seeds.json'

            with resume_csv.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=['job_position_name', 'career_objective', 'skills', 'matched_score'])
                writer.writeheader()

            seed_resume_text.write_text(json.dumps([
                {
                    'label': 'Bellman',
                    'text': (
                        'Bellman / Concierge\n'
                        'Assists guests with luggage, manages transfers, and welcomes guests.\n'
                        'Bachelor of Science in Hospitality Management\n'
                    ),
                },
                {
                    'label': 'Receptionist',
                    'text': (
                        'Receptionist\n'
                        'Answer guest complaints, inquiries, requests, and check in and check out guest transactions.\n'
                    ),
                },
            ]), encoding='utf-8')

            metadata = train_textcat_model(
                resume_csv_path=resume_csv,
                output_dir=output_dir,
                seed_resume_text_paths=[seed_resume_text],
                epochs=1,
                train_fraction=0.5,
                seed=7,
            )

            self.assertEqual(sorted(metadata['labels']), ['Bellman', 'Receptionist'])
            self.assertGreaterEqual(metadata['seed_resume_examples'], 2)

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
    def test_matcher_ignores_title_prediction_for_final_breakdown(self):
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

        self.assertNotIn('title_prediction_score', result.breakdown)

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

    def test_matcher_does_not_use_keyword_similarity_in_final_score(self):
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

        self.assertEqual(result.keyword_match_score, 0.0)
        self.assertNotIn('keyword_score', result.breakdown)
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

    def test_matcher_rebalances_soft_skills_for_non_tech_jobs(self):
        resume = ResumeProfile(
            raw_text='Front desk associate with guest service, communication, conflict resolution, scheduling, and reporting.',
            skills=['Guest Service', 'Communication', 'Conflict Resolution', 'Scheduling'],
            education=['Associate Degree'],
            experience=['Front Desk Associate with 3 years of experience handling reservations and guest concerns'],
            locations=['Manila'],
        )
        job = JobProfile(
            title='Front Desk Associate',
            raw_text='Guest service communication conflict resolution scheduling reception reservations hotel',
            required_skills=['Guest Service', 'Communication', 'Conflict Resolution'],
            preferred_skills=['Scheduling', 'Reception'],
            experience_level='mid level',
            location='Manila',
        )

        with patch.object(ResumeJobMatcher, 'calculate_job_title_prediction_score', return_value=None):
            result = ResumeJobMatcher().match_resume_to_job(resume, job)

        self.assertGreaterEqual(result.skill_match_score, 0.7)
        self.assertGreaterEqual(result.overall_score, 0.65)

    def test_get_embedding_model_is_disabled_by_default_for_fast_requests(self):
        original_flag = matcher_module.ENABLE_SEMANTIC_MATCHING
        original_model = matcher_module._embedding_model
        matcher_module.ENABLE_SEMANTIC_MATCHING = False
        matcher_module._embedding_model = None

        try:
            with patch.object(matcher_module, 'SentenceTransformer') as sentence_transformer:
                model = matcher_module.get_embedding_model()
        finally:
            matcher_module.ENABLE_SEMANTIC_MATCHING = original_flag
            matcher_module._embedding_model = original_model

        sentence_transformer.assert_not_called()
        self.assertIsNone(model)

    def test_estimate_experience_level_uses_explicit_years(self):
        matcher = ResumeJobMatcher()

        self.assertEqual(matcher._estimate_experience_level(['Worked 1 year in support roles']), 1)
        self.assertEqual(matcher._estimate_experience_level(['Accumulated 3 years of backend engineering']), 2)
        self.assertEqual(matcher._estimate_experience_level(['Built systems for 5 years']), 3)
        self.assertEqual(matcher._estimate_experience_level(['Led teams for 8 years']), 4)
        self.assertEqual(matcher._estimate_experience_level(['Directed programs for 10 years']), 5)

    def test_calculate_education_match_scores_elementary_only_as_thirty_percent(self):
        matcher = ResumeJobMatcher()

        score = matcher.calculate_education_match(
            ['Elementary Graduate'],
            ['Bachelor of Science in Computer Science'],
        )

        self.assertEqual(score, 0.3)

    def test_calculate_education_match_scores_high_school_only_as_sixty_percent(self):
        matcher = ResumeJobMatcher()

        score = matcher.calculate_education_match(
            ['High School Graduate'],
            ['Bachelor of Science in Computer Science'],
        )

        self.assertEqual(score, 0.6)

    def test_calculate_education_match_uses_same_tiers_when_jd_has_no_requirement(self):
        matcher = ResumeJobMatcher()

        elementary_score = matcher.calculate_education_match(['Elementary Graduate'], [])
        high_school_score = matcher.calculate_education_match(['High School Graduate'], [])
        college_score = matcher.calculate_education_match(['Bachelor of Science in Information Technology'], [])

        self.assertEqual(elementary_score, 0.3)
        self.assertEqual(high_school_score, 0.6)
        self.assertEqual(college_score, 1.0)

    def test_matcher_includes_education_breakdown_when_jd_has_no_requirement(self):
        resume = ResumeProfile(
            raw_text='Office clerk with report writing and accounting experience.',
            skills=['Report Writing'],
            education=['Bachelor of Science in Accountancy'],
            experience=['Accounting clerk with 8 years of experience'],
        )
        job = JobProfile(
            title='Office Clerk',
            raw_text='Office clerk report writing administrative support',
            required_skills=['Report Writing'],
            preferred_skills=[],
            education_requirements=[],
        )

        with patch.object(ResumeJobMatcher, 'calculate_job_title_prediction_score', return_value=None):
            result = ResumeJobMatcher().match_resume_to_job(resume, job)

        self.assertEqual(result.education_match_score, 1.0)
        self.assertIn('education_score', result.breakdown)
        self.assertEqual(result.breakdown['education_score'], 0.2)
        self.assertLess(result.overall_score, 1.0)

    def test_matcher_keeps_education_weight_at_twenty_percent_of_overall_score(self):
        matcher = ResumeJobMatcher()

        overall_score = matcher._calculate_weighted_score([
            ('skill_score', 0.2, 0.50),
            ('experience_score', 0.3, 0.30),
            ('education_score', 1.0, 0.20),
        ])

        self.assertEqual(overall_score, 0.39)

    def test_matcher_does_not_renormalize_to_hundred_percent_when_only_education_scores(self):
        matcher = ResumeJobMatcher()

        overall_score = matcher._calculate_weighted_score([
            ('skill_score', None, 0.50),
            ('experience_score', None, 0.30),
            ('education_score', 1.0, 0.20),
        ])

        self.assertEqual(overall_score, 0.2)

    def test_calculate_education_match_scores_college_or_higher_as_full_score(self):
        matcher = ResumeJobMatcher()

        bachelor_score = matcher.calculate_education_match(
            ['Bachelor of Science in Information Technology'],
            ['High School Diploma'],
        )
        masters_score = matcher.calculate_education_match(
            ['Master of Science in Computer Science'],
            ['Bachelor of Science in Computer Science'],
        )
        phd_score = matcher.calculate_education_match(
            ['PhD in Data Science'],
            ['Bachelor of Science in Computer Science'],
        )

        self.assertEqual(bachelor_score, 1.0)
        self.assertEqual(masters_score, 1.0)
        self.assertEqual(phd_score, 1.0)