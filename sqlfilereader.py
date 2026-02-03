import sqlite3
import pandas as pd

df = pd.read_csv('UpdatedResumeDataSet.csv')
df.columns = df.columns.str.strip()

connection = sqlite3.connect('resumes.db')

df.to_sql('Resumes', connection, if_exists='replace')

connection.close()
