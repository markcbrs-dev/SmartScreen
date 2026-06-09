import importlib.util
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.test import SimpleTestCase
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from main.services.job_parser import JobDescriptionParser
from main.services.resume_parser import ResumeParser


class StubNlp:
    def __init__(self, entities):
        self._entities = entities

    def __call__(self, text):
        return SimpleNamespace(ents=list(self._entities))


def make_entity(text: str, label: str):
    return SimpleNamespace(text=text, label_=label)


class ResumeParserNerTests(SimpleTestCase):
    def test_extract_entities_uses_named_entities_for_org_location_and_name(self):
        parser = ResumeParser()
        parser._nlp = StubNlp([
            make_entity('Jane Applicant', 'PERSON'),
            make_entity('SmartScreen Labs', 'ORG'),
            make_entity('Manila', 'GPE'),
            make_entity('SmartScreen Labs', 'ORG'),
        ])

        organizations, locations, full_name = parser.extract_entities(
            'Jane Applicant worked at SmartScreen Labs in Manila.'
        )

        self.assertEqual(organizations, ['SmartScreen Labs'])
        self.assertEqual(locations, ['Manila'])
        self.assertEqual(full_name, 'Jane Applicant')

    def test_extract_entities_falls_back_to_name_guess_when_person_entity_missing(self):
        parser = ResumeParser()
        parser._nlp = StubNlp([
            make_entity('SmartScreen Labs', 'ORG'),
            make_entity('Cebu City', 'GPE'),
        ])

        organizations, locations, full_name = parser.extract_entities(
            'Jane Applicant\nSenior Python Developer\nSmartScreen Labs\nCebu City'
        )

        self.assertEqual(organizations, ['SmartScreen Labs'])
        self.assertEqual(locations, ['Cebu City'])
        self.assertEqual(full_name, 'Jane Applicant')

    @skipUnless(importlib.util.find_spec('en_core_web_sm') is not None, 'en_core_web_sm is not installed')
    def test_extract_entities_uses_real_spacy_model_when_enabled(self):
        parser = ResumeParser()

        with patch('main.services.resume_parser.ENABLE_SPACY', True):
            organizations, locations, full_name = parser.extract_entities(
                'Barack Obama\nProfessional Experience\nWorked at Microsoft in Seattle.'
            )

        self.assertIn('Microsoft', organizations)
        self.assertIn('Seattle', locations)
        self.assertEqual(full_name, 'Barack Obama')


class ResumeParserExtractionTests(SimpleTestCase):
    def test_guess_name_accepts_name_label_prefix(self):
        parser = ResumeParser()

        full_name = parser._guess_name(
            'Name: Jane Applicant\n'
            'jane@example.com\n'
            '09123456789\n'
            'Python Developer\n'
        )

        self.assertEqual(full_name, 'Jane Applicant')

    def test_guess_name_skips_role_title_and_normalizes_last_name_first(self):
        parser = ResumeParser()

        full_name = parser._guess_name(
            'Python Developer\n'
            'Applicant, Jane Marie\n'
            'jane@example.com\n'
            '+63 912 345 6789\n'
        )

        self.assertEqual(full_name, 'Jane Marie Applicant')

    def test_guess_name_combines_adjacent_top_lines_for_split_full_name(self):
        parser = ResumeParser()

        full_name = parser._guess_name(
            'JUAN A.\n'
            'DELA CRUZ\n'
            'juan@example.com\n'
            '+63 912 345 6789\n'
        )

        self.assertEqual(full_name, 'JUAN A. DELA CRUZ')

    def test_extract_skills_normalizes_common_aliases(self):
        parser = ResumeParser()

        skills = parser.extract_skills('Worked with Postgres, JS, OOP, RESTful API, and ML systems.')

        self.assertIn('PostgreSQL', skills)
        self.assertIn('JavaScript', skills)
        self.assertIn('Object-Oriented Programming', skills)
        self.assertIn('REST API', skills)
        self.assertIn('Machine Learning', skills)

    def test_extract_skills_prefers_skills_section_over_ambiguous_full_text_aliases(self):
        parser = ResumeParser()

        skills = parser.extract_skills(
            'Career Objective\n'
            'Grow in IT and go above and beyond for customers.\n\n'
            'Skills\n'
            'Python\n'
            'Django\n'
        )

        self.assertEqual(skills, ['Python', 'Django'])

    def test_extract_skills_ignores_ambiguous_full_text_aliases_without_explicit_phrases(self):
        parser = ResumeParser()

        skills = parser.extract_skills(
            'Motivated applicant ready to grow in IT and go above and beyond for customers.\n'
            'Strong communication and teamwork skills.\n'
        )

        self.assertNotIn('Information Technology', skills)
        self.assertNotIn('Go', skills)

    def test_parse_uses_resume_parser_pro_as_fallback_for_basic_fields(self):
        parser = ResumeParser()
        resume_file = SimpleNamespace(name='resume.docx')

        with patch.object(parser, 'extract_text', return_value='Professional Summary\nCustomer-focused applicant'):
            with patch.object(parser, '_parse_with_resume_parser_pro', return_value={
                'name': 'Jane Applicant',
                'email': ['jane@example.com'],
                'phone_number': ['09123456789'],
            }):
                profile = parser.parse(resume_file, filename='resume.docx')

        self.assertEqual(profile.full_name, 'Jane Applicant')
        self.assertEqual(profile.email, 'jane@example.com')
        self.assertEqual(profile.phone, '+63 912 345 6789')

    def test_extract_phone_normalizes_supported_philippine_mobile_formats(self):
        parser = ResumeParser()

        self.assertEqual(parser.extract_phone('09123456789'), '+63 912 345 6789')
        self.assertEqual(parser.extract_phone('+63 912 345 6789'), '+63 912 345 6789')
        self.assertEqual(parser.extract_phone('639123456789'), '+63 912 345 6789')

    def test_parse_prefers_resume_parser_pro_name_over_local_fallback(self):
        parser = ResumeParser()
        resume_file = SimpleNamespace(name='resume.docx')

        with patch.object(parser, 'extract_text', return_value='Python Developer\nJane Applicant\njane@example.com'):
            with patch.object(parser, '_parse_with_resume_parser_pro', return_value={'name': 'Applicant, Jane'}):
                with patch.object(parser, 'extract_entities', return_value=([], [], 'Python Developer')):
                    profile = parser.parse(resume_file, filename='resume.docx')

        self.assertEqual(profile.full_name, 'Jane Applicant')

    def test_parse_normalizes_all_caps_name_from_resume_parser_pro(self):
        parser = ResumeParser()
        resume_file = SimpleNamespace(name='resume.docx')

        with patch.object(parser, 'extract_text', return_value='JUAN A. DELA CRUZ\njuan@example.com'):
            with patch.object(parser, '_parse_with_resume_parser_pro', return_value={'name': 'JUAN A. DELA CRUZ'}):
                with patch.object(parser, 'extract_entities', return_value=([], [], None)):
                    profile = parser.parse(resume_file, filename='resume.docx')

        self.assertEqual(profile.full_name, 'Juan A. Dela Cruz')

    def test_parse_normalizes_all_caps_name_from_fallback_extraction(self):
        parser = ResumeParser()
        resume_file = SimpleNamespace(name='resume.docx')

        with patch.object(parser, 'extract_text', return_value='JUAN A. DELA CRUZ\njuan@example.com'):
            with patch.object(parser, '_parse_with_resume_parser_pro', return_value={}):
                with patch.object(parser, 'extract_entities', return_value=([], [], 'JUAN A. DELA CRUZ')):
                    profile = parser.parse(resume_file, filename='resume.docx')

        self.assertEqual(profile.full_name, 'Juan A. Dela Cruz')

    def test_parse_uses_fallback_when_resume_parser_pro_only_returns_first_name(self):
        parser = ResumeParser()
        resume_file = SimpleNamespace(name='resume.docx')

        with patch.object(parser, 'extract_text', return_value='JUAN A. DELA CRUZ\njuan@example.com'):
            with patch.object(parser, '_parse_with_resume_parser_pro', return_value={'name': 'Juan'}):
                with patch.object(parser, 'extract_entities', return_value=([], [], 'JUAN A. DELA CRUZ')):
                    profile = parser.parse(resume_file, filename='resume.docx')

        self.assertEqual(profile.full_name, 'Juan A. Dela Cruz')

    def test_parse_prefers_fallback_when_it_contains_more_complete_name_parts(self):
        parser = ResumeParser()
        resume_file = SimpleNamespace(name='resume.docx')

        with patch.object(parser, 'extract_text', return_value='JUAN A. DELA CRUZ\njuan@example.com'):
            with patch.object(parser, '_parse_with_resume_parser_pro', return_value={'name': 'Juan Dela Cruz'}):
                with patch.object(parser, 'extract_entities', return_value=([], [], 'JUAN A. DELA CRUZ')):
                    profile = parser.parse(resume_file, filename='resume.docx')

        self.assertEqual(profile.full_name, 'Juan A. Dela Cruz')

    def test_parse_uses_combined_top_lines_when_primary_name_is_partial(self):
        parser = ResumeParser()
        resume_file = SimpleNamespace(name='resume.docx')

        with patch.object(parser, 'extract_text', return_value='JUAN A.\nDELA CRUZ\njuan@example.com'):
            with patch.object(parser, '_parse_with_resume_parser_pro', return_value={'name': 'Juan'}):
                with patch.object(parser, 'extract_entities', return_value=([], [], 'JUAN A. DELA CRUZ')):
                    profile = parser.parse(resume_file, filename='resume.docx')

        self.assertEqual(profile.full_name, 'Juan A. Dela Cruz')

    def test_extract_education_prefers_education_section_over_keyword_noise(self):
        parser = ResumeParser()

        education = parser.extract_education(
            'Experience\n'
            'Worked on school enrollment systems as a software engineer\n\n'
            'Education\n'
            'Bachelor of Science in Computer Science\n'
            'Example University\n\n'
            'Skills\n'
            'Python, Django\n'
        )

        self.assertIn('Bachelor of Science in Computer Science', education)
        self.assertNotIn('Worked on school enrollment systems as a software engineer', education)

    def test_extract_experience_captures_non_tech_role_lines(self):
        parser = ResumeParser()

        experience = parser.extract_experience(
            'Professional Experience\n'
            'Front Desk Associate - Welcome Hotel\n'
            'Handled guest inquiries, reservations, and daily reporting.\n\n'
            'Skills\n'
            'Customer service, scheduling\n'
        )

        self.assertTrue(any('Front Desk Associate' in line for line in experience))


class JobDescriptionParserNerTests(SimpleTestCase):
    def test_extract_company_uses_org_entity_when_no_company_header_exists(self):
        parser = JobDescriptionParser()
        parser._nlp = StubNlp([
            make_entity('SmartScreen University', 'ORG'),
            make_entity('Manila', 'GPE'),
        ])

        company = parser.extract_company(
            'We are hiring faculty members to join SmartScreen University this semester.'
        )

        self.assertEqual(company, 'SmartScreen University')

    def test_extract_location_uses_gpe_entities_when_no_location_header_exists(self):
        parser = JobDescriptionParser()
        parser._nlp = StubNlp([
            make_entity('Manila', 'GPE'),
            make_entity('Quezon City', 'GPE'),
            make_entity('Manila', 'GPE'),
        ])

        location = parser.extract_location(
            'This role supports campus operations across Manila and Quezon City.'
        )

        self.assertEqual(location, 'Manila, Quezon City')

    @skipUnless(importlib.util.find_spec('en_core_web_sm') is not None, 'en_core_web_sm is not installed')
    def test_job_parser_uses_real_spacy_model_when_enabled(self):
        parser = JobDescriptionParser()

        with patch('main.services.job_parser.ENABLE_SPACY', True):
            company = parser.extract_company('Google is hiring engineers for a new platform team.')
            location = parser.extract_location('This role is based in London and supports EMEA customers.')

        self.assertEqual(company, 'Google')
        self.assertEqual(location, 'London')


class JobDescriptionParserExtractionTests(SimpleTestCase):
    def test_extract_skills_uses_required_and_preferred_sections_with_aliases(self):
        parser = JobDescriptionParser()

        required, preferred = parser.extract_skills(
            'Requirements\n'
            '- Strong JS and Postgres experience\n'
            '- RESTful API development\n\n'
            'Preferred Qualifications\n'
            '- Kubernetes\n'
            '- Communication\n'
        )

        self.assertIn('JavaScript', required)
        self.assertIn('PostgreSQL', required)
        self.assertIn('REST API', required)
        self.assertIn('Kubernetes', preferred)
        self.assertIn('Communication', preferred)

    def test_extract_responsibilities_preserves_txt_bullets_and_dashes(self):
        parser = JobDescriptionParser()

        responsibilities = parser.extract_responsibilities(
            'Responsibilities\n'
            '• Build backend APIs\n'
            '- Review pull requests\n'
            '1. Mentor junior developers\n'
        )

        self.assertEqual(
            responsibilities,
            ['• Build backend APIs', '- Review pull requests', 'Mentor junior developers'],
        )

    def test_extract_responsibilities_preserves_docx_bullets(self):
        parser = JobDescriptionParser()
        document = Document()
        document.add_paragraph('Responsibilities')
        document.add_paragraph('Handle guest requests', style='List Bullet')
        document.add_paragraph('Prepare daily reports', style='List Bullet')

        buffer = BytesIO()
        document.save(buffer)
        buffer.seek(0)

        profile = parser.parse(buffer, filename='responsibilities.docx')

        self.assertEqual(
            profile.responsibilities,
            ['• Handle guest requests', '• Prepare daily reports'],
        )

    def test_extract_responsibilities_preserves_docx_numbering_bullets_without_bullet_style_name(self):
        parser = JobDescriptionParser()
        document = Document()
        document.add_paragraph('Responsibilities')
        bullet_paragraph = document.add_paragraph('Handle guest requests', style='Normal')
        paragraph_properties = bullet_paragraph._p.get_or_add_pPr()
        numbering_properties = OxmlElement('w:numPr')
        level = OxmlElement('w:ilvl')
        level.set(qn('w:val'), '0')
        num_id = OxmlElement('w:numId')
        num_id.set(qn('w:val'), '1')
        numbering_properties.append(level)
        numbering_properties.append(num_id)
        paragraph_properties.append(numbering_properties)

        buffer = BytesIO()
        document.save(buffer)
        buffer.seek(0)

        profile = parser.parse(buffer, filename='responsibilities.docx')

        self.assertEqual(profile.responsibilities, ['• Handle guest requests'])

    def test_extract_employment_type_preserves_full_time_and_part_time(self):
        parser = JobDescriptionParser()

        employment_type = parser.extract_employment_type(
            'Employment Type: Full-Time or Part-Time\nSchedule may vary by department.'
        )

        self.assertEqual(employment_type, 'Full-Time / Part-Time')

    def test_extract_experience_level_normalizes_year_based_fallback(self):
        parser = JobDescriptionParser()

        level = parser.extract_experience_level('Candidates must have 6 years of experience building backend systems.')

        self.assertEqual(level, 'senior')

    def test_parse_docx_two_column_job_description_table(self):
        parser = JobDescriptionParser()
        document = Document()
        document.add_paragraph('JOB DESCRIPTION')
        table = document.add_table(rows=3, cols=2)
        table.cell(0, 0).text = 'JOB TITLE'
        table.cell(0, 1).text = 'KIDS ATTENDANT'
        table.cell(1, 0).text = 'REPORTING TO:'
        table.cell(1, 1).text = 'AREA SUPERVISOR'
        table.cell(2, 0).text = 'DUTIES & RESPONSIBILITIES'
        table.cell(2, 1).text = (
            'Supervising children in their own activities.\n'
            'Organizing fun and recreational activities for children while their parents are away.\n'
            'Providing the children\'s essential needs.'
        )

        buffer = BytesIO()
        document.save(buffer)
        buffer.seek(0)

        profile = parser.parse(buffer, filename='job-description.docx')

        self.assertEqual(profile.title, 'Kids Attendant')
        self.assertEqual(profile.reporting_to, 'Area Supervisor')
        self.assertIn('Supervising children in their own activities.', profile.responsibilities)
        self.assertIn(
            'Organizing fun and recreational activities for children while their parents are away.',
            profile.responsibilities,
        )
        self.assertEqual(profile.qualifications, [])

    def test_parse_docx_hotel_style_butler_table(self):
        parser = JobDescriptionParser()
        document = Document()
        document.add_paragraph('JOB DESCRIPTION')
        table = document.add_table(rows=3, cols=2)
        table.cell(0, 0).text = 'JOB TITLE'
        table.cell(0, 1).text = 'BUTLER'
        table.cell(1, 0).text = 'REPORTING TO:'
        table.cell(1, 1).text = 'AREA SUPERVISOR'
        table.cell(2, 0).text = 'DUTIES & RESPONSIBILITIES'
        table.cell(2, 1).text = (
            'Keep the horizon club running and does her best to make sure all guests leave delighted.\n'
            'Get VIP guests as they approach the Horizon Club and provide them with assistance and offered deals.\n'
            'Prepare buffet areas and ensure that all necessary food and beverage items are presented in an appealing manner.\n'
            'Perform follow up duties to ensure continuous guest satisfaction and take orders for additional services.'
        )

        buffer = BytesIO()
        document.save(buffer)
        buffer.seek(0)

        profile = parser.parse(buffer, filename='butler-job-description.docx')

        self.assertEqual(profile.title, 'Butler')
        self.assertEqual(profile.reporting_to, 'Area Supervisor')
        self.assertEqual(profile.qualifications, [])
        self.assertIn('Guest Service', profile.required_skills)
        self.assertTrue(any(skill in profile.required_skills for skill in ['Beverage Service', 'Food Service']))
        self.assertIn('Hospitality', profile.industries)
        self.assertIn(
            'Keep the horizon club running and does her best to make sure all guests leave delighted.',
            profile.responsibilities,
        )
        self.assertIn(
            'Perform follow up duties to ensure continuous guest satisfaction and take orders for additional services.',
            profile.responsibilities,
        )

    def test_extract_job_title_recognizes_non_tech_role_titles(self):
        parser = JobDescriptionParser()

        title = parser.extract_job_title(
            'We are hiring a Front Desk Associate to welcome guests and manage reservations.',
            filename='front-desk-associate.docx',
        )

        self.assertEqual(title, 'Front Desk Associate')

    def test_extract_job_title_recognizes_hospitality_titles_from_sample(self):
        parser = JobDescriptionParser()

        bellman_title = parser.extract_job_title(
            'Bellman\nCompany: Keystone Manpower Corporation\nLocation: Quezon City',
            filename='bellman.txt',
        )
        support_title = parser.extract_job_title(
            'Ingress & Egress Support\nCompany: Keystone Manpower Corporation\nLocation: Quezon City',
            filename='ingress-egress-support.txt',
        )
        kitchen_title = parser.extract_job_title(
            'Kitchen Helper\nCompany: Keystone Manpower Corporation\nLocation: Quezon City',
            filename='kitchen-helper.txt',
        )

        self.assertEqual(bellman_title, 'Bellman')
        self.assertEqual(support_title, 'Ingress & Egress Support')
        self.assertEqual(kitchen_title, 'Kitchen Helper')

    def test_extract_skills_covers_barista_and_bartender_sample_terms(self):
        parser = JobDescriptionParser()

        required, preferred = parser.extract_skills(
            'Job Title: Barista\n'
            'Duties and Responsibilities:\n'
            '- Give customers drink menus and answer their questions regarding ingredients.\n'
            '- Take orders while paying attention to details.\n'
            '- Prepare beverages following recipes.\n'
            '- Check if brewing equipment operates properly and report any maintenance needs.\n'
            '- Comply with health and safety regulations.\n\n'
            'Job Title: Bartender\n'
            '- Mix ingredients to prepare cocktails and other drinks.\n'
            '- Present bar menu.\n'
            '- Maintain liquor inventory and consumption.\n'
            '- Collect payment for drinks served and balance all receipts.\n'
        )

        extracted = set(required + preferred)
        self.assertIn('Barista Preparation', extracted)
        self.assertIn('Bartending', extracted)
        self.assertIn('Menu Knowledge', extracted)
        self.assertIn('Inventory Management', extracted)

    def test_parse_splits_multi_job_description_document(self):
        parser = JobDescriptionParser()

        profiles = parser.parse_text(
            'JOB TITLE\nKIDS ATTENDANT\nDUTIES & RESPONSIBILITIES\n• Supervising children in their own activities.'
        )

        self.assertEqual(profiles.title, 'Kids Attendant')

        result = parser.parse_text(
            'JOB TITLE\nBARTENDER\nDUTIES & RESPONSIBILITIES\n• Prepare drinks.'
        )
        self.assertEqual(result.title, 'Bartender')

    def test_parse_text_splits_multi_role_hospitality_sample(self):
        parser = JobDescriptionParser()
        document_text = (
            'Job Title: Barista\n'
            'Company: Keystone Manpower Corporation\n'
            'Duties and Responsibilities:\n'
            '- Prepare beverages following recipes.\n\n'
            'Bartender\n'
            'Company: Keystone Manpower Corporation\n'
            'Duties and Responsibilities:\n'
            '- Mix ingredients to prepare cocktails and other drinks.\n'
        )

        profiles = parser.parse_text(document_text, filename='hospitality-roles.txt')

        self.assertIsInstance(profiles, list)
        self.assertEqual([profile.title for profile in profiles], ['Barista', 'Bartender'])

    def test_parse_returns_multiple_profiles_for_multi_jd_text(self):
        parser = JobDescriptionParser()
        document_text = (
            'JOB DESCRIPTION\nJOB TITLE\nKIDS ATTENDANT\nDUTIES & RESPONSIBILITIES\n• Supervising children.\n\n'
            'JOB DESCRIPTION\nJOB TITLE\nBARTENDER\nDUTIES & RESPONSIBILITIES\n• Preparing beverages.'
        )

        with patch.object(parser, 'extract_text', return_value=document_text):
            profiles = parser.parse(object(), filename='hotel-jobs.docx')

        self.assertEqual(len(profiles), 2)
        self.assertEqual([profile.title for profile in profiles], ['Kids Attendant', 'Bartender'])

    def test_parse_real_workspace_multi_jd_docx_file(self):
        parser = JobDescriptionParser()
        docx_path = Path(__file__).resolve().parents[2] / 'JOB-DESCRIPTION-all-works.docx'

        with docx_path.open('rb') as file_obj:
            profiles = parser.parse(file_obj, filename=docx_path.name)

        self.assertIsInstance(profiles, list)
        self.assertGreater(len(profiles), 1)
        titles = {profile.title for profile in profiles}
        self.assertIn('Kids Attendant', titles)
        self.assertIn('Butler', titles)