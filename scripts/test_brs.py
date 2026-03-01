from utils.brs_parser import fetch_and_parse_brs, rows_to_pretty_text

BRS_USERNAME = "obraztsov_v_d"   # логин из скриншота менеджера паролей
BRS_PASSWORD = "Vladislav1323:)" # пароль из скриншота
STUDENT_ID   = "16230024"

rows = fetch_and_parse_brs(STUDENT_ID, BRS_USERNAME, BRS_PASSWORD)
print(f"Найдено строк: {len(rows)}")
print(rows_to_pretty_text(rows, limit=5))