from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from docx import Document

from main.models import Applicant, JobDescription
from main.services.recruitment import sync_sample_job_description


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
	def test_apply_page_shows_uploaded_job_description_without_auto_match_option(self):
		job = sync_sample_job_description()

		response = self.client.get(reverse('apply'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Job Description')
		self.assertContains(response, job.title)
		self.assertNotContains(response, 'Auto-match me to the best job')

	def test_apply_matches_selected_job_and_stores_resume_analysis(self):
		job = sync_sample_job_description()

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
						'Python Java JavaScript teaching and research experience',
						'Bachelor of Science in Computer Science',
						'Artificial Intelligence and Data Science projects',
					],
				),
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		applicant = Applicant.objects.get(email='jane@example.com')
		self.assertEqual(applicant.position, job.title)
		self.assertEqual(applicant.applied_job, job)
		self.assertTrue(applicant.job_preference_locked)
		self.assertIn('Python', applicant.parsed_skills)
		self.assertGreaterEqual(applicant.match_score, 1)


class JobUploadTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='hr@example.com', password='secret123')
		self.client.force_login(self.user)

	def test_dashboard_uses_sample_job_description(self):
		response = self.client.get(reverse('dashboard'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Active Job Description')
		self.assertContains(response, 'Job Description Sample.txt')
		self.assertTrue(JobDescription.objects.exists())
		self.assertContains(response, 'Computer Science Professor')

	def test_dashboard_refreshes_existing_applicant_match_scores(self):
		Applicant.objects.create(
			first_name='Mark',
			last_name='Cabreros',
			contact_number='+63 912 345 6789',
			email='mark@example.com',
			resume=build_docx_file(
				'mark-resume.docx',
				[
					'Mark Cabreros',
					'mark@example.com',
					'+63 912 345 6789',
					'Python Java JavaScript Artificial Intelligence Data Science',
					'Bachelor of Science in Computer Science',
					'Teaching and research experience',
				],
			),
		)

		response = self.client.get(reverse('dashboard'))

		self.assertEqual(response.status_code, 200)
		applicant = Applicant.objects.get(email='mark@example.com')
		self.assertEqual(applicant.position, 'Computer Science Professor')
		self.assertIsNotNone(applicant.applied_job)
		self.assertGreater(applicant.match_score, 0)
		self.assertTrue(applicant.match_notes)

	def test_sync_sample_job_description_creates_active_job(self):
		job = sync_sample_job_description()

		self.assertIsNotNone(job)
		self.assertEqual(JobDescription.objects.count(), 1)
		self.assertIn('Computer Science', job.title)
		self.assertTrue(job.required_skills)

	def test_logout_clears_dashboard_session(self):
		response = self.client.post(reverse('logout'), follow=True)

		self.assertRedirects(response, reverse('login'))
		self.assertFalse('_auth_user_id' in self.client.session)
		self.assertContains(response, 'Logged out successfully.')
