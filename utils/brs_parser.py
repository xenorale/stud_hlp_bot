from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional, List, Tuple
from bs4 import BeautifulSoup
import httpx

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 25
MAX_RETRIES = 3

@dataclass
class BrsRow:
    year: str
    semester: int
    course: int
    subject: str
    control: str
    teacher_short: str
    att1: Optional[float]
    att2: Optional[float]
    att3: Optional[float]
    attendance_pct: Optional[float]
    weighted_score: Optional[float]
    exam_score: Optional[float]
    extra_score: Optional[float]
    final_score: Optional[float]
    final_text: str
    moodle_url: Optional[str]
    lessons_url: Optional[str] = None

@dataclass
class LessonsStats:
    total: int
    attended: int
    skipped: int

def _cell_text(cell) -> str:
    if cell is None:
        return ''
    return cell.get_text(' ', strip=True)

def _to_float(s: str) -> Optional[float]:
    s = (s or '').strip().replace(',', '.')
    if not s or s == '—':
        return None
    try:
        return float(s)
    except ValueError:
        return None

async def _safe_request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    last_exc: Optional[BaseException] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
            return resp
        except httpx.RequestError as exc:
            last_exc = exc
    raise RuntimeError(f'Не удалось выполнить запрос к {url}: {last_exc}')

async def brs_login(username: str, password: str, cookies: Optional[dict]=None) -> Tuple[httpx.AsyncClient, dict]:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'}
    client = httpx.AsyncClient(verify=False, headers=headers, follow_redirects=True)
    if cookies:
        client.cookies.update(cookies)
    base = 'https://www.cs.vsu.ru'
    login_url = f'{base}/brs/login'
    
    if cookies:
        try:
            resp = await _safe_request(client, 'GET', login_url)
            if 'Выход' in resp.text or 'logout' in resp.text.lower():
                return (client, dict(client.cookies))
        except Exception:
            pass
        client.cookies.clear()
        
    payload = {'login': username, 'password': password, 'button_login': 'Вход'}
    resp = await _safe_request(client, 'POST', login_url, data=payload)
    if 'Выход' not in resp.text and 'logout' not in resp.text.lower():
        from bs4 import BeautifulSoup as _BS
        _soup = _BS(resp.text, 'lxml')
        _alert = _soup.select_one('.alert, .error, .invalid-feedback, #error')
        _msg = _alert.get_text(strip=True) if _alert else resp.text[:300]
        raise RuntimeError(f'Авторизация не удалась (статус {resp.status_code}).\nСообщение сервера: {_msg}')
    return (client, dict(client.cookies))

async def fetch_brs_html(student_id: str, client: httpx.AsyncClient) -> str:
    url = f'https://www.cs.vsu.ru/brs/att_marks_report_student/{student_id}'
    resp = await _safe_request(client, 'GET', url)
    if 'table' not in resp.text:
        raise RuntimeError(f'Страница не содержит таблицу. URL: {resp.url}\nНачало: {resp.text[:500]}')
    return resp.text

def parse_brs_att_marks(html: str) -> List[BrsRow]:
    soup = BeautifulSoup(html, 'lxml')
    table = soup.select_one('table.table.table-bordered')
    if not table:
        all_tables = soup.find_all('table')
        raise RuntimeError(f"Не нашёл table.table.table-bordered. Таблиц на странице: {len(all_tables)}. Классы: {[t.get('class') for t in all_tables[:5]]}")
    body_rows = table.select('tbody tr')
    result: List[BrsRow] = []
    for tr in body_rows:
        if not tr.has_attr('data-att-mark-id'):
            continue
        tds = tr.find_all(['td', 'th'], recursive=False)
        if len(tds) < 10:
            continue
        year = _cell_text(tds[0])
        semester = int(_cell_text(tds[1]) or 0)
        course = int(_cell_text(tds[2]) or 0)
        subject_cell = tds[3]
        subject = _cell_text(subject_cell)
        moodle_a = subject_cell.select_one('a[title*="Moodle"]')
        moodle_url = moodle_a.get('href') if moodle_a else None
        control = _cell_text(tds[4])
        teacher_short = _cell_text(tds[5])
        att1 = _to_float(_cell_text(tds[6]))
        att2 = _to_float(_cell_text(tds[7]))
        att3 = _to_float(_cell_text(tds[8]))
        attendance_cell = tds[9]
        att_a = attendance_cell.find('a')
        attendance_pct = _to_float(_cell_text(att_a)) if att_a else _to_float(_cell_text(attendance_cell))
        lessons_url = att_a.get('href') if att_a else None
        weighted_score = _to_float(_cell_text(tds[10]))
        exam_score = _to_float(_cell_text(tds[11]))
        extra_score = _to_float(_cell_text(tds[12]))
        final_score = _to_float(_cell_text(tds[13]))
        final_text = _cell_text(tds[14])
        result.append(BrsRow(year=year, semester=semester, course=course, subject=subject, control=control, teacher_short=teacher_short, att1=att1, att2=att2, att3=att3, attendance_pct=attendance_pct, weighted_score=weighted_score, exam_score=exam_score, extra_score=extra_score, final_score=final_score, final_text=final_text, moodle_url=moodle_url, lessons_url=lessons_url))
    return result

def parse_lessons_html(html: str) -> LessonsStats:
    soup = BeautifulSoup(html, 'lxml')
    rows = soup.select('table tbody tr')
    total = 0
    attended = 0
    skipped = 0
    for tr in rows:
        tds = tr.find_all('td')
        if len(tds) < 7:
            continue
        total += 1
        mark = _cell_text(tds[6]).strip()
        if mark == '+':
            attended += 1
        elif mark == '-':
            skipped += 1
    return LessonsStats(total=total, attended=attended, skipped=skipped)

async def fetch_lessons_stats(lessons_url: str, client: httpx.AsyncClient) -> LessonsStats:
    base = 'https://www.cs.vsu.ru'
    url = lessons_url if lessons_url.startswith('http') else base + lessons_url
    resp = await _safe_request(client, 'GET', url)
    return parse_lessons_html(resp.text)

async def fetch_and_parse_brs(student_id: str, username: str, password: str, cookies: Optional[dict]=None) -> Tuple[List[BrsRow], dict]:
    logger.info(f'BRS: Начинаю сессию для {username}')
    try:
        client, valid_cookies = await brs_login(username, password, cookies)
    except Exception as e:
        logger.error(f'BRS: Ошибка авторизации: {e}')
        raise
    
    try:
        logger.info(f'BRS: Загружаю отчет для {student_id}')
        html = await fetch_brs_html(student_id, client)
        logger.info('BRS: Парсю данные')
        rows = parse_brs_att_marks(html)
        logger.info(f'BRS: Успешно загружено {len(rows)} строк')
        return (rows, valid_cookies)
    except Exception as e:
        logger.error(f'BRS: Ошибка при получении/парсинге: {e}')
        raise
    finally:
        await client.aclose()
        logger.info('BRS: Клиент закрыт')

def rows_to_pretty_text(rows: List[BrsRow], limit: int=15) -> str:
    lines: list[str] = []
    for r in rows[:limit]:
        att = ' / '.join([str(x) if x is not None else '—' for x in (r.att1, r.att2, r.att3)])
        lines.append(f"📚 {r.subject}\n Семестр {r.semester} | {r.control}\n Аттестации: {att}\n Посещаемость: {(r.attendance_pct if r.attendance_pct is not None else '—')}%\n Итог. балл: {(r.final_score if r.final_score is not None else '—')} — {r.final_text or '—'}")
    if len(rows) > limit:
        lines.append(f'\n...и ещё {len(rows) - limit} предметов.')
    return '\n\n'.join(lines)