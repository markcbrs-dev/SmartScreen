import importlib.util
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from main.services.job_parser import JobDescriptionParser
from main.services.matcher import ResumeJobMatcher
from main.services.resume_parser import ResumeParser
from main.services.skills import canonicalize_skill_list


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

    def test_extract_entities_prefers_more_complete_guessed_name_over_shorter_person_entity(self):
        parser = ResumeParser()
        parser._nlp = StubNlp([
            make_entity('Marlou R.', 'PERSON'),
            make_entity('Antipolo City', 'GPE'),
        ])

        organizations, locations, full_name = parser.extract_entities(
            'Marlou R.\n'
            'Saysay\n'
            'marlousaysay99@gmail.com\n'
            'Antipolo City\n'
        )

        self.assertEqual(organizations, [])
        self.assertEqual(locations, ['Antipolo City'])
        self.assertEqual(full_name, 'Marlou R. Saysay')

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
    def test_extract_text_from_pdf_prefers_pymupdf_when_available(self):
        parser = ResumeParser()
        pdf_file = BytesIO(b'%PDF-1.4 fake pdf bytes')

        class StubPage:
            def get_text(self):
                return 'Marlou resume text'

        class StubPdf:
            def __enter__(self):
                return [StubPage()]

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch('main.services.resume_parser.fitz.open', return_value=StubPdf()) as fitz_open:
            with patch('main.services.resume_parser.pdfplumber') as pdfplumber_module:
                with patch('main.services.resume_parser.PdfReader') as pdf_reader:
                    text = parser._extract_text_from_pdf(pdf_file)

        fitz_open.assert_called_once()
        pdfplumber_module.open.assert_not_called()
        pdf_reader.assert_not_called()
        self.assertEqual(text, 'Marlou resume text')

    def test_split_name_components_separates_middle_initial_from_last_name(self):
        parser = ResumeParser()

        first_name, middle_name, last_name = parser.split_name_components('Marlou R. Saysay')

        self.assertEqual(first_name, 'Marlou')
        self.assertEqual(middle_name, 'R.')
        self.assertEqual(last_name, 'Saysay')

    def test_split_name_components_keeps_multiple_given_names_before_middle_initial(self):
        parser = ResumeParser()

        first_name, middle_name, last_name = parser.split_name_components('Juan Carlo M. Dela Cruz')

        self.assertEqual(first_name, 'Juan Carlo')
        self.assertEqual(middle_name, 'M.')
        self.assertEqual(last_name, 'Dela Cruz')

    def test_split_name_components_keeps_multiple_given_names_when_no_middle_initial_exists(self):
        parser = ResumeParser()

        first_name, middle_name, last_name = parser.split_name_components('Maria Clara Dela Cruz')

        self.assertEqual(first_name, 'Maria Clara')
        self.assertEqual(middle_name, '')
        self.assertEqual(last_name, 'Dela Cruz')

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

    def test_guess_name_recovers_split_name_from_interleaved_pdf_text(self):
        parser = ResumeParser()

        full_name = parser._guess_name(
            'EMPLOYMENT HISTORY:\n'
            'CONSULTANT - LEAD SOFTWARE DEVELOPER\n'
            'Irvine Analytics Solutions | January 2021 - Present\n'
            'JOSE\n'
            'ASSISTANT TO SENIOR PROJECT MANAGER\n'
            'Solaire Resorts & Casino Manila\n'
            'LORENZO\n'
            'Internship (300 hours) | September 2018 - October 2018\n'
            'FAELMAN\n'
            'ACADEMIC BACKGROUND:\n'
            'Web Developer\n'
            'POLYTECHNIC UNIVERSITY OF THE PHILIPPINES\n'
        )

        self.assertEqual(full_name, 'JOSE LORENZO FAELMAN')

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

    def test_extract_skills_excludes_degree_domains_from_education_context(self):
        parser = ResumeParser()

        skills = parser.extract_skills(
            'Academic Background\n'
            'Polytechnic University of the Philippines\n'
            'Diploma in Information Technology\n'
            'Bachelor of Science in Computer Science\n'
        )

        self.assertNotIn('Information Technology', skills)
        self.assertNotIn('Computer Science', skills)

    def test_extract_skills_keeps_information_technology_when_explicitly_used_as_skill(self):
        parser = ResumeParser()

        skills = parser.extract_skills(
            'Skills\n'
            'Information Technology\n'
            'Python\n'
            'Technical support\n'
        )

        self.assertNotIn('Information Technology', skills)
        self.assertIn('Python', skills)

    def test_canonicalize_skill_list_drops_degree_domain_labels(self):
        skills = canonicalize_skill_list(['Information Technology', 'Computer Science', 'Python'])

        self.assertEqual(skills, ['Python'])

    def test_matcher_missing_skills_excludes_degree_domain_labels(self):
        matcher = ResumeJobMatcher()

        score, matching_skills, missing_skills = matcher.calculate_skill_match(
            ['Python'],
            ['Information Technology', 'Computer Science', 'Python'],
            [],
        )

        self.assertGreater(score, 0)
        self.assertEqual(matching_skills, ['Python'])
        self.assertNotIn('Information Technology', missing_skills)
        self.assertNotIn('Computer Science', missing_skills)

    def test_matcher_missing_skills_only_tracks_unmatched_required_skills(self):
        matcher = ResumeJobMatcher()

        score, matching_skills, missing_skills = matcher.calculate_skill_match(
            ['Python'],
            ['Python', 'Django'],
            ['Docker', 'Kubernetes'],
        )

        self.assertGreater(score, 0)
        self.assertEqual(matching_skills, ['Python'])
        self.assertIn('Django', missing_skills)
        self.assertNotIn('Docker', missing_skills)
        self.assertNotIn('Kubernetes', missing_skills)

    def test_matcher_missing_skills_falls_back_to_preferred_when_required_is_empty(self):
        matcher = ResumeJobMatcher()

        score, matching_skills, missing_skills = matcher.calculate_skill_match(
            ['CSS'],
            [],
            ['CSS', 'Python', 'Communication'],
        )

        self.assertGreater(score, 0)
        self.assertEqual(matching_skills, ['CSS'])
        self.assertIn('Python', missing_skills)
        self.assertIn('Communication', missing_skills)

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

    def test_parse_skips_resume_parser_pro_when_basic_fields_are_found_locally(self):
        parser = ResumeParser()
        resume_file = SimpleNamespace(name='resume.docx')

        with patch.object(
            parser,
            'extract_text',
            return_value='Jane Applicant\njane@example.com\n09123456789\nPython Developer',
        ):
            with patch.object(parser, 'extract_entities', return_value=([], [], 'Jane Applicant')):
                with patch.object(parser, '_parse_with_resume_parser_pro') as pro_parser:
                    profile = parser.parse(resume_file, filename='resume.docx')

        pro_parser.assert_not_called()
        self.assertEqual(profile.full_name, 'Jane Applicant')
        self.assertEqual(profile.email, 'jane@example.com')
        self.assertEqual(profile.phone, '+63 912 345 6789')

    def test_parse_uses_gemini_enrichment_when_core_fields_are_missing(self):
        parser = ResumeParser()
        resume_file = SimpleNamespace(name='resume.docx')
        gemini_client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(return_value=SimpleNamespace(text='''
                    {
                        "full_name": "Jane Applicant",
                        "email": "jane@example.com",
                        "phone": "09123456789",
                        "skills": ["Python", "Django"],
                        "education": ["Example University"],
                        "experience": ["Software Engineer at Example Corp"],
                        "organizations": ["Example Corp"],
                        "locations": ["Manila"]
                    }
                ''')),
            ),
        )

        with patch.object(parser, 'extract_text', return_value='Professional Summary\nMotivated applicant'):
            with patch.object(parser, 'extract_entities', return_value=([], [], None)):
                with patch.object(parser, '_parse_with_resume_parser_pro', return_value={}):
                    with patch.object(parser, '_get_gemini_client', return_value=gemini_client):
                        profile = parser.parse(resume_file, filename='resume.docx')

        gemini_client.models.generate_content.assert_called_once()
        self.assertEqual(profile.full_name, 'Jane Applicant')
        self.assertEqual(profile.email, 'jane@example.com')
        self.assertEqual(profile.phone, '+63 912 345 6789')
        self.assertEqual(profile.skills, ['Python', 'Django'])
        self.assertEqual(profile.education, ['Example University'])
        self.assertEqual(profile.experience, ['Software Engineer at Example Corp'])
        self.assertEqual(profile.organizations, ['Example Corp'])
        self.assertEqual(profile.locations, ['Manila'])

    def test_parse_prefers_gemini_for_full_name_when_available(self):
        parser = ResumeParser()
        resume_file = SimpleNamespace(name='resume.docx')
        gemini_client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(return_value=SimpleNamespace(text='''
                    {
                        "full_name": "Jane Q. Applicant",
                        "email": null,
                        "phone": null,
                        "skills": [],
                        "education": [],
                        "experience": [],
                        "organizations": [],
                        "locations": []
                    }
                ''')),
            ),
        )

        with patch.object(
            parser,
            'extract_text',
            return_value='Jane Applicant\njane@example.com\n09123456789\nPython Developer',
        ):
            with patch.object(parser, 'extract_entities', return_value=(['Example Corp'], ['Manila'], 'Jane Applicant')):
                with patch.object(parser, 'extract_skills', return_value=['Python']):
                    with patch.object(parser, 'extract_education', return_value=['Example University']):
                        with patch.object(parser, 'extract_experience', return_value=['Software Engineer']):
                            with patch.object(parser, '_parse_with_resume_parser_pro', return_value={}):
                                with patch.object(parser, '_get_gemini_client', return_value=gemini_client):
                                    profile = parser.parse(resume_file, filename='resume.docx')

        gemini_client.models.generate_content.assert_called_once()
        self.assertEqual(profile.full_name, 'Jane Q. Applicant')

    def test_parse_uses_gemini_to_enrich_resume_entities_even_when_local_fields_exist(self):
        parser = ResumeParser()
        resume_file = SimpleNamespace(name='resume.docx')
        gemini_client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(return_value=SimpleNamespace(text='''
                    {
                        "full_name": "Jane Q. Applicant",
                        "email": null,
                        "phone": null,
                        "skills": ["Python", "Django"],
                        "education": ["Example University"],
                        "experience": ["Software Engineer at Example Corp"],
                        "organizations": ["Example Corp"],
                        "locations": ["Manila"]
                    }
                ''')),
            ),
        )

        with patch.object(parser, 'extract_text', return_value='Jane Applicant\njane@example.com\n09123456789\nPython Developer'):
            with patch.object(parser, 'extract_entities', return_value=(['Local Corp'], ['Quezon City'], 'Jane Applicant')):
                with patch.object(parser, 'extract_skills', return_value=['Python']):
                    with patch.object(parser, 'extract_education', return_value=['Local University']):
                        with patch.object(parser, 'extract_experience', return_value=['Junior Developer']):
                            with patch.object(parser, '_parse_with_resume_parser_pro', return_value={}):
                                with patch.object(parser, '_get_gemini_client', return_value=gemini_client):
                                    profile = parser.parse(resume_file, filename='resume.docx')

        self.assertEqual(profile.skills, ['Python', 'Django'])
        self.assertEqual(profile.education, ['Local University', 'Example University'])
        self.assertEqual(profile.experience, ['Junior Developer', 'Software Engineer at Example Corp'])
        self.assertEqual(profile.organizations, ['Local Corp', 'Example Corp'])
        self.assertEqual(profile.locations, ['Quezon City', 'Manila'])

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

    def test_parse_uses_filename_as_last_resort_for_name(self):
        parser = ResumeParser()
        resume_file = SimpleNamespace(name='Jose Lorenzo Faelman - Resume.pdf')

        with patch.object(parser, 'extract_text', return_value='Unreadable extracted text'):
            with patch.object(parser, '_parse_with_resume_parser_pro', return_value={}):
                with patch.object(parser, 'extract_entities', return_value=([], [], None)):
                    profile = parser.parse(resume_file, filename=resume_file.name)

        self.assertEqual(profile.full_name, 'Jose Lorenzo Faelman')

    def test_parse_prefers_filename_when_empty_text_conflicts_with_resume_parser_pro_name(self):
        parser = ResumeParser()
        resume_file = SimpleNamespace(name='Jerome Lemuel Faelman - Resume.pdf')

        with patch.object(parser, 'extract_text', return_value=''):
            with patch.object(parser, '_parse_with_resume_parser_pro', return_value={'name': 'FEU Roosevelt'}):
                with patch.object(parser, 'extract_entities', return_value=([], [], None)):
                    profile = parser.parse(resume_file, filename=resume_file.name)

        self.assertEqual(profile.full_name, 'Jerome Lemuel Faelman')

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

    def test_guess_name_rejects_skill_framework_descriptor_as_applicant_name(self):
        parser = ResumeParser()

        full_name = parser._guess_name(
            'Django Framework\n'
            'juan@example.com\n'
            '+63 912 345 6789\n'
        )

        self.assertIsNone(full_name)

    def test_guess_name_rejects_canonical_skill_phrase_as_applicant_name(self):
        parser = ResumeParser()

        full_name = parser._guess_name(
            'Machine Learning\n'
            'ml@example.com\n'
            '+63 912 345 6789\n'
        )

        self.assertIsNone(full_name)

    def test_guess_name_accepts_real_name_when_skill_phrase_appears_first(self):
        parser = ResumeParser()

        full_name = parser._guess_name(
            'Django Framework\n'
            'Juan Dela Cruz\n'
            'juan@example.com\n'
            '+63 912 345 6789\n'
        )

        self.assertEqual(full_name, 'Juan Dela Cruz')

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


            def test_extract_education_prefers_most_recent_school_from_interleaved_pdf_text(self):
                parser = ResumeParser()

                education = parser.extract_education(
                    'ACADEMIC BACKGROUND:\n'
                    'POLYTECHNIC UNIVERSITY OF THE PHILIPPINES\n'
                    'Institute of Technology\n'
                    'Class of 2021\n'
                    'Diploma in Information Communication Technology\n'
                    'ROOSEVELT COLLEGE INC. - CAINTA\n'
                    'From 2016 to 2018\n'
                    'Senior High Humanities and Social Science Strand\n'
                )

                self.assertGreaterEqual(len(education), 1)
                self.assertEqual(education[0], 'POLYTECHNIC UNIVERSITY OF THE PHILIPPINES')
        company = parser.extract_company(
            'We are hiring faculty members to join SmartScreen University this semester.'
        )

        self.assertEqual(company, 'SmartScreen University')

    def test_extract_location_uses_gpe_entities_when_no_location_header_exists(self):
        parser = JobDescriptionParser()

    def test_extract_experience_prefers_role_titles_from_interleaved_pdf_text(self):
        parser = ResumeParser()

        experience = parser.extract_experience(
            'ASSISTANT TO SENIOR PROJECT MANAGER\n'
            'Solaire Resorts & Casino Manila\n'
            'Internship (300 hours) | September 2018 - October 2018\n'
            'EMPLOYMENT HISTORY:\n'
            'CONSULTANT - LEAD SOFTWARE DEVELOPER\n'
            'Irvine Analytics Solutions | January 2021 - Present\n'
            '• Develop their system using CodeIgniter3 / PHP.\n'
            '• Develop web application with Python and Django Framework.\n'
        )

        self.assertGreaterEqual(len(experience), 2)
        self.assertEqual(experience[0], 'CONSULTANT - LEAD SOFTWARE DEVELOPER')
        self.assertIn('ASSISTANT TO SENIOR PROJECT MANAGER', experience)
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
    def test_parse_uses_gemini_to_enrich_job_entities(self):
        parser = JobDescriptionParser()
        gemini_client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(return_value=SimpleNamespace(text='''
                    {
                        "title": "Backend Engineer",
                        "reporting_to": "Engineering Manager",
                        "company": "Example Corp",
                        "location": "Manila",
                        "employment_type": "Full-Time",
                        "experience_level": "senior",
                        "salary_range": "$5000-$7000",
                        "remote_option": true,
                        "required_skills": ["Python", "Django"],
                        "preferred_skills": ["Docker"],
                        "responsibilities": ["Build APIs"],
                        "qualifications": ["Bachelor's degree"],
                        "education_requirements": ["Bachelor of Science in Computer Science"],
                        "certifications": ["AWS Certified"],
                        "benefits": ["Health insurance"],
                        "industries": ["Technology"]
                    }
                ''')),
            ),
        )

        with patch.object(parser, '_get_gemini_client', return_value=gemini_client):
            profile = parser.parse_text('Hiring for a backend role supporting our Manila team.', filename='backend-role.txt')

        gemini_client.models.generate_content.assert_called_once()
        self.assertEqual(profile.title, 'Backend Engineer')
        self.assertEqual(profile.reporting_to, 'Engineering Manager')
        self.assertEqual(profile.company, 'Example Corp')
        self.assertEqual(profile.location, 'Manila')
        self.assertEqual(profile.employment_type, 'Full-Time')
        self.assertEqual(profile.experience_level, 'senior')
        self.assertEqual(profile.salary_range, '$5000-$7000')
        self.assertTrue(profile.remote_option)
        self.assertIn('Python', profile.required_skills)
        self.assertIn('Django', profile.required_skills)
        self.assertIn('Docker', profile.preferred_skills)
        self.assertEqual(profile.responsibilities, ['Build APIs'])
        self.assertEqual(profile.qualifications, ["Bachelor's degree"])
        self.assertEqual(profile.education_requirements, ['Bachelor Of Science In Computer Science'])
        self.assertEqual(profile.certifications, ['Aws Certified'])
        self.assertEqual(profile.benefits, ['Health insurance'])
        self.assertEqual(profile.industries, ['Technology'])

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

    def test_extract_skills_excludes_degree_domains_from_qualification_lines(self):
        parser = JobDescriptionParser()

        required, preferred = parser.extract_skills(
            'Qualifications\n'
            '- Graduate of BSIT/BSCS\n'
            '- With Master\'s degree in IT/CS or any IT-related programs is a plus\n'
            '- A holder of applicable National Certification in CSS and Programming is a plus\n'
        )

        self.assertNotIn('Information Technology', required)
        self.assertNotIn('Computer Science', required)
        self.assertNotIn('Information Technology', preferred)
        self.assertNotIn('Computer Science', preferred)
        self.assertIn('CSS', preferred)
        self.assertIn('Programming', preferred)

    def test_extract_skills_keeps_plain_qualification_bullets_required(self):
        parser = JobDescriptionParser()

        required, preferred = parser.extract_skills(
            'Qualifications\n'
            '- Strong Python and Django experience\n'
            '- Excellent communication skills\n'
            '- Docker experience is a plus\n'
        )

        self.assertIn('Python', required)
        self.assertIn('Django', required)
        self.assertIn('Communication', required)
        self.assertIn('Docker', preferred)
        self.assertNotIn('Docker', required)

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