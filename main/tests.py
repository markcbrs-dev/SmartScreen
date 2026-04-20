from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from docx import Document

from main.models import Applicant, JobDescription


def build_docx_file(name: str, paragraphs: list[str]) -> SimpleUploadedFile:
	buffer = BytesIO()
	document = Document()
	for paragraph in paragraphs:
		document.add_paragraph(paragraph)
	document.save(buffer)
	buffer.seek(0)
	return SimpleUploadedFile(
		name,
		buffer.getvalue(),
		content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
	)


class ApplicantFlowTests(TestCase):
	def test_apply_matches_selected_job_and_stores_resume_analysis(self):
		job = JobDescription.objects.create(
			title='Backend Developer',
			description_file=SimpleUploadedFile('backend.txt', b'Python Django REST API', content_type='text/plain'),
			required_skills=['Python', 'Django'],
			preferred_skills=['REST API'],
			raw_text='Backend Developer role requiring Python, Django, and REST API experience.',
			remote_option=True,
		)

		response = self.client.post(
			reverse('apply'),
			{
				'first_name': 'Jane',
				'last_name': 'Applicant',
				'contact': '+63 912 345 6789',
				'email': 'jane@example.com',
				'job_id': str(job.id),
				'resume': build_docx_file(
					'resume.docx',
					[
						'Jane Applicant',
						'jane@example.com',
						'+63 912 345 6789',
						'Python Django REST API remote work experience',
						'Bachelor of Science in Computer Science',
					],
				),
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		applicant = Applicant.objects.get(email='jane@example.com')
		self.assertEqual(applicant.position, 'Backend Developer')
		self.assertEqual(applicant.applied_job, job)
		self.assertTrue(applicant.job_preference_locked)
		self.assertIn('Python', applicant.parsed_skills)
		self.assertGreaterEqual(applicant.match_score, 1)


class JobUploadTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='hr@example.com', password='secret123')
		self.client.force_login(self.user)

	def test_upload_job_description_rematches_existing_unlocked_applicant(self):
		applicant = Applicant.objects.create(
			first_name='Mark',
			last_name='Candidate',
			contact_number='+63 900 000 0000',
			email='mark@example.com',
			resume=build_docx_file(
				'mark_resume.docx',
				[
					'Mark Candidate',
					'mark@example.com',
					'Python Django SQL',
					'Remote work experience across distributed teams',
				],
			),
			resume_text='Mark Candidate Python Django SQL remote work experience',
			parsed_skills=['Python', 'Django', 'SQL'],
			parsed_education=['Bachelor degree in Information Technology'],
			parsed_experience=['Senior Python developer with remote experience'],
			parsed_locations=['Manila'],
		)

		response = self.client.post(
			reverse('upload_job_description'),
			{
				'title': 'Python Engineer',
				'description_file': SimpleUploadedFile(
					'python_engineer.txt',
					b'Job Title: Python Engineer\nLocation: Remote\nRequirements\n- Python\n- Django\n- SQL\n',
					content_type='text/plain',
				),
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		applicant.refresh_from_db()
		self.assertEqual(JobDescription.objects.count(), 1)
		self.assertEqual(applicant.position, 'Python Engineer')
		self.assertIsNotNone(applicant.applied_job)
		self.assertGreaterEqual(applicant.match_score, 1)
