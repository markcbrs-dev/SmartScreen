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