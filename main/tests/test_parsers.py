import importlib.util
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.test import SimpleTestCase

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