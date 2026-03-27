# Student Helper Bot — ВГУ

Telegram-бот помощника студента ВГУ (факультет КН).

## Функционал

- **Оценки из БРС** — авторизация на cs.vsu.ru и парсинг таблицы оценок по семестрам
- **Калькулятор посещаемости** — симуляция влияния пропусков на взвешенный балл
- **FAQ** — ответы на частые вопросы (редактируется через `data/faq.json`)

## Установка

```bash
git clone <repo>
cd student-helper-bot
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

## Настройка (.env)

Скопируй `.env.example` в `.env` и заполни:

```
BOT_TOKEN=        # токен от @BotFather
BRS_USERNAME=     # логин на cs.vsu.ru/brs (обычно фамилия_и_о)
BRS_PASSWORD=     # пароль от cs.vsu.ru/brs
BRS_STUDENT_ID=   # числовой ID студента (см. ниже)
```

**Как найти BRS_STUDENT_ID:**
Зайди на cs.vsu.ru/brs → Оценки. В адресной строке будет:
`.../brs/att_marks_report_student/16230024` — число в конце и есть ID.

## Запуск

```bash
python main.py
```

При первом запуске автоматически создаётся `bot.db` со всеми таблицами.

## Структура проекта

```
main.py                  # бот, FSM-обработчики
config.py                # загрузка .env
database/
  models.py              # SQLAlchemy-модели
  db.py                  # engine, сессии, init_db()
utils/
  brs_parser.py          # парсер cs.vsu.ru/brs
  brs_helpers.py         # форматирование данных БРС
  calculators.py         # калькуляторы посещаемости
data/
  faq.json               # вопросы и ответы FAQ
scripts/
  test_brs.py            # ручная проверка парсера
```
