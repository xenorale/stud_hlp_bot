# scripts/debug_login_form.py
import requests
from bs4 import BeautifulSoup

s = requests.Session()
r = s.get("https://www.cs.vsu.ru/brs/login", timeout=25)
soup = BeautifulSoup(r.text, "lxml")

print("=== TITLE ===")
print(soup.title.text if soup.title else "no title")

print("\n=== FORM ===")
form = soup.find("form")
if form:
    print("action:", form.get("action"))
    print("method:", form.get("method"))
    for inp in form.find_all(["input", "button", "select"]):
        print(f"  [{inp.name}] name={inp.get('name')!r:30} type={inp.get('type')!r:15} value={inp.get('value','')[:40]!r}")
else:
    print("ФОРМА НЕ НАЙДЕНА")
    print(r.text[:2000])