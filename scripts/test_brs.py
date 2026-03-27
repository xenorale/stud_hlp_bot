import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from utils.brs_parser import fetch_and_parse_brs, rows_to_pretty_text

BRS_USERNAME = os.getenv("BRS_USERNAME")
BRS_PASSWORD = os.getenv("BRS_PASSWORD")
STUDENT_ID   = os.getenv("BRS_STUDENT_ID")

if not all([BRS_USERNAME, BRS_PASSWORD, STUDENT_ID]):
    print("❌ Заполни BRS_USERNAME, BRS_PASSWORD, BRS_STUDENT_ID в .env")
    sys.exit(1)

rows = fetch_and_parse_brs(STUDENT_ID, BRS_USERNAME, BRS_PASSWORD)
print(f"Найдено строк: {len(rows)}")
print(rows_to_pretty_text(rows, limit=5))
