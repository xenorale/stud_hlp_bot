import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BRS_USERNAME = os.getenv("BRS_USERNAME")
BRS_PASSWORD = os.getenv("BRS_PASSWORD")
BRS_STUDENT_ID = os.getenv("BRS_STUDENT_ID")  # ID студента из ВГУ
GROUP_NUMBER = int(os.getenv("GROUP_NUMBER", "10"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "")  # https://... публичный URL Mini App
API_PORT = int(os.getenv("API_PORT", "8000"))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в .env")