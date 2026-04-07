from django.db import models

# Create your models here.
class Resume(models.Model):
    name= models.CharField(max_length=200)

    def __str__(self):
        return self.name


class Details(models.Model):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE)
    text = models.CharField(max_length=300)
    complete = models.BooleanField()


class Applicant(models.Model):
    STATUS_CHOICES = [
        ('NEW', 'New'),
        ('REVIEWING', 'Reviewing'),
        ('INTERVIEW', 'Interview'),
        ('OFFER', 'Offer'),
    ]

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    contact_number = models.CharField(max_length=50)
    email = models.EmailField()
    resume = models.FileField(upload_to='resumes/')
    position = models.CharField(max_length=100, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='NEW')
    match_score = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name} <{self.email}>"