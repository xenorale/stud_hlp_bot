import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BRS_USERNAME = os.getenv("BRS_USERNAME")
BRS_PASSWORD = os.getenv("BRS_PASSWORD")
BRS_STUDENT_ID = os.getenv("BRS_STUDENT_ID")  # ID студента из ВГУ

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в .env")