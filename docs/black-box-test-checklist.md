# Black-Box Test Checklist

Repeat this checklist from the Django project root to verify the main user-facing flows without relying on implementation details.

## Prerequisites

1. Start the app:

```powershell
.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

2. Make sure at least one job description exists in the database so the applicant form and dashboard have data to render.
3. Prepare one valid resume file (`.docx` or `.pdf`) and one invalid file (for example `invalid.pdf` for job upload or `invalid.txt` for resume upload, depending on the test).
4. Have one approved HR staff account available for dashboard tests.

## Public Flow

1. Open `http://127.0.0.1:8000/`.
Expected: home page shows Applicant and HR entry points.

2. Open `http://127.0.0.1:8000/apply/`.
Expected: applicant form loads, target-position dropdown is populated, and a job description preview is visible.

3. Submit a valid application with name, contact number, email, target position, and a valid resume file.
Expected: success message appears and the form resets.

4. Submit the same form with an invalid resume type.
Expected: server rejects the upload with an error message.

## HR Authentication

1. Open `http://127.0.0.1:8000/dashboard/` while logged out.
Expected: redirect to login.

2. Register a new HR account.
Expected: redirected to login with a pending-approval message.

3. Try logging in with the unapproved HR account.
Expected: login is blocked with a pending-approval message.

4. Log in with an approved staff account.
Expected: dashboard loads successfully.

## Dashboard Actions And File Management

1. On the dashboard, confirm applicant totals and job counts render.
Expected: stat cards and applicant table load without errors.

2. Open the `Active Job Positions` panel.
Expected: upload form and uploaded job cards are visible.

3. Upload an invalid job description file type.
Expected: upload is rejected with an error message.

4. Upload a valid TXT or DOCX job description.
Expected: success message appears and a new uploaded job card is added.

5. Open the uploaded job description preview/dialog.
Expected: full description content is viewable from the dashboard.

6. Remove the newly uploaded job description.
Expected: success message appears and the card disappears.

7. In the applicant table, use `View Resume`.
Expected: resume opens through `/resumes/<filename>`, not `/media/resumes/<filename>`.

8. Move an applicant from `NEW` to `INTERVIEW`, then move the applicant back if needed.
Expected: status changes immediately and the confirmation message matches the action.

9. Open the status/contact actions for an interview applicant.
Expected: contact dialog renders email and phone actions without layout breakage.

10. Remove a test applicant from the dashboard if one was created for testing.
Expected: applicant row disappears and a success message is shown.

## Resume Access Control

1. While logged out, request `http://127.0.0.1:8000/resumes/<known-filename>`.
Expected: redirect to login.

2. While logged out, request `http://127.0.0.1:8000/media/resumes/<known-filename>`.
Expected: file is not served.

3. While logged in as staff, request `http://127.0.0.1:8000/resumes/<known-filename>`.
Expected: file downloads successfully.

## Cleanup

1. Remove any job descriptions uploaded only for testing.
2. Remove any applicants created only for testing.
3. Remove any temporary HR accounts created only for testing.