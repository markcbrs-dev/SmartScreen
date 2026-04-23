from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from docx import Document

from main.models import Applicant, JobDescription
from main.services.recruitment import replace_active_job_description


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


def build_text_file(name: str, content: str) -> SimpleUploadedFile:
	return SimpleUploadedFile(name, content.encode('utf-8'), content_type='text/plain')


class ApplicantFlowTests(TestCase):
	def test_home_redirects_to_apply_page(self):
		response = self.client.get(reverse('home'))

		self.assertRedirects(response, reverse('apply'))

	def test_apply_page_shows_uploaded_job_description_without_auto_match_option(self):
		job = replace_active_job_description(build_text_file(
			'computer-science-professor.txt',
			'Job Title: Computer Science Professor\nCompany: SmartScreen University\nLocation: Manila\nResponsibilities\nTeach computer science courses\nQualifications\nBachelor of Science in Computer Science\nPython Java JavaScript research teaching',
		))

		response = self.client.get(reverse('apply'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Select a target position')
		self.assertContains(response, 'Job Description')
		self.assertContains(response, job.title)
		self.assertNotContains(response, 'Auto-match me to the best job')
		self.assertContains(response, 'id="applicantLayout" class="layout layout-collapsed"', html=False)
		self.assertContains(response, 'id="jobDescriptionCard" class="card job-card is-collapsed"', html=False)

	def test_apply_matches_selected_job_and_stores_resume_analysis(self):
		job = replace_active_job_description(build_text_file(
			'computer-science-professor.txt',
			'Job Title: Computer Science Professor\nCompany: SmartScreen University\nLocation: Manila\nResponsibilities\nTeach computer science courses\nQualifications\nBachelor of Science in Computer Science\nPython Java JavaScript Artificial Intelligence Data Science teaching research',
		))

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
		self.assertContains(response, 'Application submitted successfully.')
		self.assertNotContains(response, 'Best match:')
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

	def test_dashboard_uploads_job_description(self):
		response = self.client.post(
			reverse('dashboard'),
			{
				'action': 'upload_job_description',
				'job_description_file': build_text_file(
					'computer-science-professor.txt',
					'Job Title: Computer Science Professor\nCompany: SmartScreen University\nLocation: Manila\nEmployment Type: Full Time\nPython Java JavaScript teaching research',
				),
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Uploaded Job Description')
		self.assertTrue(JobDescription.objects.exists())
		self.assertContains(response, 'Computer Science Professor')
		self.assertContains(response, 'Uploaded job description: Computer Science Professor.')
		self.assertContains(response, 'View')
		self.assertNotContains(response, 'Source:')

	def test_dashboard_keeps_existing_job_cards_when_uploading_new_job(self):
		self.client.post(
			reverse('dashboard'),
			{
				'action': 'upload_job_description',
				'job_description_file': build_text_file(
					'computer-science-professor.txt',
					'Job Title: Computer Science Professor\nCompany: SmartScreen University\nLocation: Manila\nPython Java JavaScript teaching research',
				),
			},
		)

		response = self.client.post(
			reverse('dashboard'),
			{
				'action': 'upload_job_description',
				'job_description_file': build_text_file(
					'backend-engineer.txt',
					'Job Title: Backend Engineer\nCompany: SmartScreen Labs\nLocation: Remote\nPython Django APIs',
				),
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(JobDescription.objects.count(), 2)
		self.assertContains(response, 'Computer Science Professor')
		self.assertContains(response, 'Backend Engineer')

	def test_dashboard_refreshes_existing_applicant_match_scores(self):
		replace_active_job_description(build_text_file(
			'computer-science-professor.txt',
			'Job Title: Computer Science Professor\nCompany: SmartScreen University\nLocation: Manila\nPython Java JavaScript Artificial Intelligence Data Science teaching research',
		))

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

	def test_dashboard_keeps_applicant_matched_to_selected_job(self):
		selected_job = replace_active_job_description(build_text_file(
			'computer-science-professor.txt',
			'Job Title: Computer Science Professor\nCompany: SmartScreen University\nLocation: Manila\nPython Java JavaScript teaching research',
		))
		replace_active_job_description(build_text_file(
			'backend-engineer.txt',
			'Job Title: Backend Engineer\nCompany: SmartScreen Labs\nLocation: Remote\nPython Django APIs microservices',
		))

		Applicant.objects.create(
			first_name='Jane',
			last_name='Applicant',
			contact_number='+63 912 345 6789',
			email='jane-selected@example.com',
			resume=build_docx_file(
				'jane-selected.docx',
				[
					'Jane Applicant',
					'jane-selected@example.com',
					'+63 912 345 6789',
					'Python Java JavaScript teaching research',
					'Bachelor of Science in Computer Science',
				],
			),
			applied_job=selected_job,
			job_preference_locked=True,
			position=selected_job.title,
		)

		response = self.client.get(reverse('dashboard'))

		self.assertEqual(response.status_code, 200)
		applicant = Applicant.objects.get(email='jane-selected@example.com')
		self.assertEqual(applicant.applied_job, selected_job)
		self.assertEqual(applicant.position, selected_job.title)

	def test_dashboard_removes_uploaded_job_description(self):
		job = replace_active_job_description(build_text_file(
			'backend-role.txt',
			'Job Title: Backend Engineer\nCompany: SmartScreen Labs\nLocation: Remote\nPython Django APIs',
		))

		response = self.client.post(
			reverse('dashboard'),
			{'action': 'remove_job_description', 'job_id': str(job.id)},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(JobDescription.objects.count(), 0)
		self.assertContains(response, 'Uploaded job description removed.')

	def test_logout_clears_dashboard_session(self):
		response = self.client.post(reverse('logout'), follow=True)

		self.assertRedirects(response, reverse('login'))
		self.assertFalse('_auth_user_id' in self.client.session)
		self.assertContains(response, 'Logged out successfully.')
