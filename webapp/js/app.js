const tg = window.Telegram?.WebApp;
const API = "";

if (tg) { tg.ready(); tg.expand(); }

const userId   = tg?.initDataUnsafe?.user?.id         || null;
const userName = tg?.initDataUnsafe?.user?.first_name || null;

// ─── State ───────────────────────────────────────────────────────────────────
const state = {
  profile: null,
  schedule: [],
  brs: [],
  faq: [],
  currentDay: null,
  currentWeek: detectCurrentWeek(),
  currentSemester: null,
  calcSubjectIdx: null,
  availableGroups: {},
  scheduleLoaded: false,
  brsLoaded: false,
  faqLoaded: false,
  reminderSettings: { enabled: false, minutes_before: 15 },
};

const DAYS_SHORT  = ["Пн","Вт","Ср","Чт","Пт","Сб"];
const DAYS_FULL   = ["Понедельник","Вторник","Среда","Четверг","Пятница","Суббота"];
const MONTH_NAMES = ["января","февраля","марта","апреля","мая","июня",
                     "июля","августа","сентября","октября","ноября","декабря"];
const DAY_NAMES   = ["Воскресенье","Понедельник","Вторник","Среда","Четверг","Пятница","Суббота"];

// ─── Utils ───────────────────────────────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  opts.headers = { "ngrok-skip-browser-warning": "1", ...(opts.headers || {}) };
  const r = await fetch(API + path, opts);
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(err.detail || r.statusText);
  }
  return r.json();
}

function haptic(type = "light") {
  tg?.HapticFeedback?.impactOccurred?.(type);
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function todayDayFull() {
  const d = new Date().getDay();
  return d === 0 ? null : DAYS_FULL[d - 1];
}

function estimateSemesterProgress() {
  const now = new Date();
  const month = now.getMonth();
  let semStart, semEnd;
  if (month >= 8) {
    semStart = new Date(now.getFullYear(), 8, 1);
    semEnd   = new Date(now.getFullYear() + 1, 0, 25);
  } else if (month <= 0) {
    semStart = new Date(now.getFullYear() - 1, 8, 1);
    semEnd   = new Date(now.getFullYear(), 0, 25);
  } else {
    semStart = new Date(now.getFullYear(), 1, 1);
    semEnd   = new Date(now.getFullYear(), 5, 15);
  }
  const totalMs   = semEnd - semStart;
  const elapsedMs = Math.max(0, now - semStart);
  const totalWeeks   = Math.round(totalMs   / (7 * 86400000));
  const elapsedWeeks = Math.min(Math.round(elapsedMs / (7 * 86400000)), totalWeeks);
  const remainWeeks  = Math.max(0, totalWeeks - elapsedWeeks);
  return { held: Math.max(1, elapsedWeeks * 2), future: Math.max(1, remainWeeks * 2) };
}

function detectCurrentWeek() {
  const now = new Date();
  const year = now.getMonth() >= 8 ? now.getFullYear() : now.getFullYear() - 1;
  const semStart = new Date(year, 8, 1);
  const dow = semStart.getDay();
  const monday = new Date(semStart);
  monday.setDate(semStart.getDate() - (dow === 0 ? 6 : dow - 1));
  const weekNum = Math.floor((now - monday) / 86400000 / 7) + 1;
  return weekNum % 2 === 1 ? "num" : "den";
}

function weekLabel(w) { return w === "num" ? "Числитель" : "Знаменатель"; }

function getLessonType(text) {
  const t = (text || "").toLowerCase();
  if (/лек/.test(t))                       return "lec";
  if (/лаб/.test(t))                       return "lab";
  if (/пр\.|практ|практика/.test(t))       return "pr";
  if (/сем\.|семин/.test(t))               return "sem";
  return "other";
}

const TYPE_BADGE = {
  lec:   ["badge-lec",  "Лекция"],
  lab:   ["badge-lab",  "Лаб"],
  pr:    ["badge-pr",   "Практика"],
  sem:   ["badge-sem",  "Семинар"],
  other: [null, null],
};

function getLessonText(lesson, subgroup, week) {
  if (subgroup === 1) return week === "num" ? lesson.sub1_num : lesson.sub1_den;
  if (subgroup === 2) return week === "num" ? lesson.sub2_num : lesson.sub2_den;
  return week === "num" ? lesson.sub1_num : lesson.sub1_den;
}

function parseLesson(text) {
  if (!text?.trim()) return null;
  const lines = text.split("\n").map(s => s.trim()).filter(Boolean);
  return { name: lines[0], meta: lines.slice(1).join(" · ") };
}

// ─── Tasks (localStorage) ────────────────────────────────────────────────────
function loadTasks() {
  try { return JSON.parse(localStorage.getItem("student_tasks") || "[]"); }
  catch { return []; }
}
function saveTasks(tasks) {
  localStorage.setItem("student_tasks", JSON.stringify(tasks));
}
function addTaskFromInput() {
  const inp = document.getElementById("task-input");
  const text = inp?.value?.trim();
  if (!text) return;
  haptic("medium");
  const tasks = loadTasks();
  tasks.unshift({ id: Date.now(), text, done: false });
  saveTasks(tasks);
  if (inp) inp.value = "";
  renderTaskList();
}
function toggleTask(id) {
  haptic("light");
  const tasks = loadTasks();
  const t = tasks.find(t => t.id === id);
  if (t) { t.done = !t.done; saveTasks(tasks); }
  renderTaskList();
}
function deleteTask(id) {
  haptic("light");
  saveTasks(loadTasks().filter(t => t.id !== id));
  renderTaskList();
}
function renderTaskList() {
  const container = document.getElementById("task-list");
  if (!container) return;
  const tasks = loadTasks();
  const LIMIT = 6;
  const shown = tasks.slice(0, LIMIT);
  const checkSvg = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;
  container.innerHTML = shown.map(t => `
    <div class="task-item ${t.done ? 'done' : ''}">
      <div class="task-check" onclick="toggleTask(${t.id})">${t.done ? checkSvg : ''}</div>
      <span class="task-text">${escapeHtml(t.text)}</span>
      <span class="task-delete" onclick="deleteTask(${t.id})">×</span>
    </div>`).join('') +
    (!tasks.length ? `<div style="color:var(--hint);font-size:13px;padding:6px 0 2px">Пусто — добавь задачу 👆</div>` : '') +
    (tasks.length > LIMIT ? `<div style="font-size:12px;color:var(--hint);margin-top:6px">+${tasks.length - LIMIT} ещё</div>` : '');
}

// ─── Skeleton helpers ─────────────────────────────────────────────────────────
function skelLessonCards(n = 3) {
  return Array.from({length: n}, () => `
    <div class="skel-card" style="display:flex;gap:10px;align-items:center">
      <div class="skeleton skel-line short" style="height:36px;width:44px;border-radius:8px;flex-shrink:0"></div>
      <div style="flex:1">
        <div class="skeleton skel-line long" style="margin-bottom:6px"></div>
        <div class="skeleton skel-line short"></div>
      </div>
    </div>`).join("");
}
function skelSubjectCards(n = 4) {
  return Array.from({length: n}, () => `
    <div class="skel-card">
      <div style="display:flex;justify-content:space-between;margin-bottom:10px">
        <div class="skeleton skel-line" style="width:60%;height:16px"></div>
        <div class="skeleton" style="width:22px;height:22px;border-radius:50%"></div>
      </div>
      <div class="skeleton skel-line medium" style="margin-bottom:6px"></div>
      <div class="skeleton skel-line" style="height:5px;border-radius:3px"></div>
    </div>`).join("");
}

// ─── Render lesson card ───────────────────────────────────────────────────────
function renderLessonCard(lesson, subgroup, week) {
  const text = getLessonText(lesson, subgroup, week);
  const parsed = parseLesson(text);
  if (!parsed) return null;
  const type = getLessonType(text);
  const [badgeClass, badgeText] = TYPE_BADGE[type];
  const badge = badgeClass ? `<span class="type-badge ${badgeClass}">${badgeText}</span>` : "";
  return `
    <div class="lesson-card ${type}">
      <div class="lesson-color-line"></div>
      <div class="lesson-inner">
        <div class="lesson-time">${lesson.time.replace("-","–")}</div>
        <div class="lesson-body">
          <div class="lesson-name">${escapeHtml(parsed.name)}</div>
          ${parsed.meta || badge ? `<div class="lesson-meta">${badge}${escapeHtml(parsed.meta)}</div>` : ""}
        </div>
      </div>
    </div>`;
}

// ─── Navigation ──────────────────────────────────────────────────────────────
const PAGE_ORDER = ["home","schedule","brs","calc","profile"];
let currentPageName = null;

function goTo(name) {
  haptic("light");
  if (name === currentPageName) return;
  const prevIdx = PAGE_ORDER.indexOf(currentPageName);
  const nextIdx = PAGE_ORDER.indexOf(name);
  const goingRight = nextIdx > prevIdx;
  if (currentPageName) {
    const oldEl = document.getElementById(`page-${currentPageName}`);
    oldEl?.classList.remove("active");
    oldEl?.classList.add(goingRight ? "exit-left" : "exit-right");
    setTimeout(() => oldEl?.classList.remove("exit-left","exit-right"), 250);
  }
  currentPageName = name;
  document.querySelectorAll(".nav-item").forEach(b => b.classList.remove("active"));
  document.getElementById(`nav-${name}`)?.classList.add("active");
  const newEl = document.getElementById(`page-${name}`);
  newEl?.classList.remove("exit-left","exit-right");
  requestAnimationFrame(() => {
    newEl?.style.setProperty("transform", goingRight ? "translateX(24px)" : "translateX(-24px)");
    requestAnimationFrame(() => {
      newEl?.style.removeProperty("transform");
      newEl?.classList.add("active");
    });
  });
  document.getElementById(`page-${name}`)?.scrollTo?.(0, 0);
  if (name === "home")     renderHomePage();
  if (name === "schedule") renderSchedulePage();
  if (name === "brs")      renderBrsPage();
  if (name === "calc")     renderCalcPage();
  if (name === "profile")  renderProfilePage();
}

// ─── HOME ─────────────────────────────────────────────────────────────────────
function renderHomePage() {
  const el = document.getElementById("page-home");
  const today = todayDayFull();
  const week = state.currentWeek;
  const now = new Date();

  const greetLine = userName
    ? `<div class="home-greeting">Привет, ${escapeHtml(userName)} 👋</div>`
    : "";

  const header = `
    <div class="home-hero">
      <div class="home-date-block">
        ${greetLine}
        <div class="home-weekday">${DAY_NAMES[now.getDay()]}</div>
        <div class="home-date">${now.getDate()} ${MONTH_NAMES[now.getMonth()]}</div>
      </div>
      <div class="week-pill">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        ${weekLabel(week)}
      </div>
    </div>`;

  if (!state.profile) {
    el.innerHTML = header + `
      <div class="empty-state"><div class="empty-icon">⚙️</div>
      <div class="empty-text">Настрой профиль чтобы видеть расписание</div></div>
      <button class="btn" onclick="goTo('profile')">Настроить профиль</button>`;
    return;
  }

  el.innerHTML = header +
    `<div class="home-section-title">Пары сегодня</div>
     <div id="home-lessons">${state.scheduleLoaded ? buildHomeLessons(today, week) : skelLessonCards(3)}</div>` +
    `<div class="home-section-title">Задачи</div>
     <div class="card" style="padding:14px">
       <div class="task-add-row">
         <input class="task-add-input" id="task-input" placeholder="Добавить задачу..."
           onkeydown="if(event.key==='Enter')addTaskFromInput()">
         <button class="task-add-btn" onclick="addTaskFromInput()">+</button>
       </div>
       <div id="task-list"></div>
     </div>` +
    `<div class="home-section-title">Успеваемость</div>
     <div id="home-brs">${state.brsLoaded ? buildHomeBrs() : skelSubjectCards(1)}</div>` +
    `<div style="margin-top:14px">
       <button class="btn btn-secondary" onclick="goTo('schedule')">Полное расписание →</button>
     </div>`;

  renderTaskList();

  if (!state.scheduleLoaded) {
    loadSchedule().then(() => {
      const slot = document.getElementById("home-lessons");
      if (slot) slot.innerHTML = buildHomeLessons(today, week);
    }).catch(() => {
      const slot = document.getElementById("home-lessons");
      if (slot) slot.innerHTML = `<div class="home-no-lessons">Не удалось загрузить расписание</div>`;
    });
  }
  if (!state.brsLoaded) {
    loadBrs().then(() => {
      const slot = document.getElementById("home-brs");
      if (slot) slot.innerHTML = buildHomeBrs();
    }).catch(() => {
      const slot = document.getElementById("home-brs");
      if (slot) slot.innerHTML = `<div class="home-no-lessons">Не удалось загрузить оценки</div>`;
    });
  }
}

function buildHomeLessons(today, week) {
  if (!today) return `<div class="home-no-lessons">Сегодня воскресенье 🎉<br>Отдыхай</div>`;
  const subgroup = state.profile?.subgroup ?? 0;
  const dayLessons = state.schedule.filter(l => l.day === today);
  let cards = ""; let count = 0;
  for (const l of dayLessons) {
    const card = renderLessonCard(l, subgroup, week);
    if (card) { cards += card; count++; }
  }
  return count ? cards : `<div class="home-no-lessons">Пар нет 🎉</div>`;
}

function buildHomeBrs() {
  const latest = state.brs.filter(r => r.semester === state.currentSemester);
  const good = latest.filter(r => r.grade_icon?.includes("✅")).length;
  const warn = latest.filter(r => r.grade_icon?.includes("⚠")).length;
  const bad  = latest.filter(r => r.grade_icon?.includes("❌")).length;
  return `
    <div class="brs-summary-grid">
      <div class="brs-stat-card"><div class="brs-stat-icon">✅</div>
        <div class="brs-stat-count">${good}</div><div class="brs-stat-label">Хорошо</div></div>
      <div class="brs-stat-card"><div class="brs-stat-icon">⚠️</div>
        <div class="brs-stat-count">${warn}</div><div class="brs-stat-label">Среднее</div></div>
      <div class="brs-stat-card"><div class="brs-stat-icon">❌</div>
        <div class="brs-stat-count">${bad}</div><div class="brs-stat-label">Низкое</div></div>
    </div>`;
}

// ─── SCHEDULE ────────────────────────────────────────────────────────────────
async function loadSchedule(force = false) {
  if (!state.profile) return;
  if (!force && state.scheduleLoaded) return;
  const data = await apiFetch(`/api/schedule?course=${state.profile.course}&group=${state.profile.group}`);
  state.schedule = data;
  state.scheduleLoaded = true;
}

function renderSchedulePage() {
  const el = document.getElementById("page-schedule");
  if (!state.profile) {
    el.innerHTML = `<div class="page-header">Расписание</div>
      <div class="empty-state"><div class="empty-icon">⚙️</div>
      <div class="empty-text">Сначала настрой профиль</div></div>
      <button class="btn" onclick="goTo('profile')">Настроить</button>`;
    return;
  }
  if (!state.scheduleLoaded) {
    el.innerHTML = `<div class="page-header">Расписание</div>${skelLessonCards(4)}`;
  }
  loadSchedule().then(() => renderScheduleView(el)).catch(e => {
    el.innerHTML = `<div class="page-header">Расписание</div>
      <div class="empty-state"><div class="empty-icon">❌</div><div class="empty-text">${e.message}</div></div>`;
  });
}

function renderScheduleView(el) {
  const today = todayDayFull();
  const subgroup = state.profile?.subgroup ?? 0;
  const week = state.currentWeek;
  const daysWithLessons = DAYS_FULL.filter(d =>
    state.schedule.some(l => l.day === d && getLessonText(l, subgroup, week)?.trim())
  );
  if (!state.currentDay || !DAYS_FULL.includes(state.currentDay)) {
    state.currentDay = (today && daysWithLessons.includes(today))
      ? today : (daysWithLessons[0] ?? DAYS_FULL[0]);
  }
  const weekTabsHtml = `
    <div class="week-tabs">
      <div class="week-tab ${week==='num'?'active':''}" onclick="switchWeek('num')">Числитель</div>
      <div class="week-tab ${week==='den'?'active':''}" onclick="switchWeek('den')">Знаменатель</div>
    </div>
    <div class="week-auto-hint">Автоопределено: сейчас <b>${weekLabel(detectCurrentWeek())}</b></div>`;
  const daysHtml = DAYS_FULL.map((d, i) => {
    const active  = d === state.currentDay;
    const isToday = d === today;
    return `<div class="day-chip ${active?'active':''} ${isToday&&!active?'today-chip':''}"
      onclick="switchDay('${d}')">${DAYS_SHORT[i]}</div>`;
  }).join("");
  const dayLessons = state.schedule.filter(l => l.day === state.currentDay);
  let lessonsHtml = ""; let count = 0;
  for (const l of dayLessons) {
    const card = renderLessonCard(l, subgroup, week);
    if (card) { lessonsHtml += card; count++; }
  }
  if (!count) lessonsHtml = `<div class="no-lessons">Пар нет 🎉</div>`;
  el.innerHTML = `
    <div class="page-header">Расписание</div>
    ${weekTabsHtml}
    <div class="days-scroll">${daysHtml}</div>
    ${lessonsHtml}
    <button class="btn btn-secondary" onclick="refreshSchedule()" style="margin-top:8px">🔄 Обновить</button>`;
}

function switchWeek(w) { haptic("light"); state.currentWeek = w; renderScheduleView(document.getElementById("page-schedule")); }
function switchDay(d)  { haptic("light"); state.currentDay = d;  renderScheduleView(document.getElementById("page-schedule")); }
function refreshSchedule() { state.scheduleLoaded = false; state.schedule = []; renderSchedulePage(); }

// ─── BRS ─────────────────────────────────────────────────────────────────────
async function loadBrs(force = false) {
  if (!force && state.brsLoaded) return;
  const data = await apiFetch("/api/brs");
  state.brs = data;
  state.brsLoaded = true;
  if (!state.currentSemester) {
    const sems = [...new Set(data.map(r => r.semester))].sort((a,b) => b-a);
    state.currentSemester = sems[0] ?? null;
  }
}

function renderBrsPage() {
  const el = document.getElementById("page-brs");
  if (!state.brsLoaded) {
    el.innerHTML = `<div class="page-header">Оценки</div>${skelSubjectCards(5)}`;
  }
  loadBrs().then(() => renderBrsView(el)).catch(e => {
    el.innerHTML = `<div class="page-header">Оценки</div>
      <div class="empty-state"><div class="empty-icon">❌</div><div class="empty-text">${e.message}</div></div>`;
  });
}

function renderBrsView(el) {
  const rows = state.brs;
  const semesters = [...new Set(rows.map(r => r.semester))].sort((a,b) => b-a);
  if (!semesters.length) {
    el.innerHTML = `<div class="page-header">Оценки</div>
      <div class="empty-state"><div class="empty-icon">📭</div><div class="empty-text">Нет данных</div></div>`;
    return;
  }
  const semTabs = semesters.map(s =>
    `<div class="sem-chip ${s===state.currentSemester?'active':''}" onclick="switchSemester(${s})">${s} сем.</div>`
  ).join("");
  const semRows = rows.filter(r => r.semester === state.currentSemester);
  const subjects = semRows.map(r => {
    const pct = r.attendance_pct ?? 0;
    const barClass = pct >= 85 ? "green" : pct >= 60 ? "yellow" : "red";
    const atts = [r.att1, r.att2, r.att3].filter(v => v != null);
    return `
      <div class="subject-card">
        <div class="subject-header">
          <div class="subject-name">${escapeHtml(r.subject)}</div>
          <div class="grade-icon">${r.grade_icon}</div>
        </div>
        <div class="subject-meta">
          <div class="meta-item"><span class="meta-label">Посещ. </span><span class="meta-value">${r.attendance_pct ?? "—"}%</span></div>
          <div class="meta-item"><span class="meta-label">Итог </span><span class="meta-value">${r.final_score ?? r.final_text ?? "—"}</span></div>
          ${atts.length ? `<div class="meta-item"><span class="meta-label">Атт. </span><span class="meta-value">${atts.join(" / ")}</span></div>` : ""}
          ${r.exam_score != null ? `<div class="meta-item"><span class="meta-label">Экзамен </span><span class="meta-value">${r.exam_score}</span></div>` : ""}
        </div>
        ${r.attendance_pct != null ? `
          <div class="att-bar-row">
            <div class="att-bar-wrap"><div class="att-bar ${barClass}" style="width:${Math.min(100,pct)}%"></div></div>
            <span class="att-pct-label">${pct}%</span>
          </div>` : ""}
      </div>`;
  }).join("");
  el.innerHTML = `<div class="page-header">Оценки</div>
    <div class="semester-tabs">${semTabs}</div>
    ${subjects}`;
}

function switchSemester(s) { haptic("light"); state.currentSemester = s; renderBrsView(document.getElementById("page-brs")); }

// ─── CALC ─────────────────────────────────────────────────────────────────────
function renderCalcPage() {
  const el = document.getElementById("page-calc");
  if (!state.brsLoaded) {
    el.innerHTML = `<div class="page-header">Калькулятор</div>${skelSubjectCards(3)}`;
    loadBrs().then(() => renderCalcForm(el)).catch(() => renderCalcManual(el));
    return;
  }
  renderCalcForm(el);
}

function renderCalcForm(el = document.getElementById("page-calc")) {
  const rows = state.brs;
  const prog = estimateSemesterProgress();
  const list = rows.slice(0, 10).map((r, i) => `
    <div class="subject-option ${state.calcSubjectIdx===i?'selected':''}" onclick="selectCalcSubject(${i})">
      <span class="subject-option-name">${r.subject.length>34?r.subject.slice(0,34)+"…":r.subject}</span>
      <span class="subject-option-pct">${r.attendance_pct ?? "—"}%</span>
    </div>`).join("");
  const inputsVisible = state.calcSubjectIdx !== null;
  el.innerHTML = `
    <div class="page-header">Калькулятор</div>
    <div class="card">
      <div class="card-title">Выбери предмет</div>
      ${list}
    </div>
    <div id="calc-inputs" style="display:${inputsVisible?'block':'none'}">
      <div class="card">
        <div class="card-title">Сколько пар пропустишь?</div>
        <div class="form-group">
          <label class="form-label">Планирую пропустить</label>
          <input class="form-input" id="inp-skips" type="number" value="0" min="0">
        </div>
        <div id="lessons-hint" style="font-size:12px;color:var(--hint);margin-top:8px"></div>
        <details style="margin-top:10px">
          <summary style="font-size:12px;color:var(--hint);cursor:pointer;padding:4px 0">Дополнительно (авто)</summary>
          <div style="margin-top:8px;display:flex;flex-direction:column;gap:10px">
            <div class="form-group">
              <label class="form-label">Уже прошло пар</label>
              <input class="form-input" id="inp-held" type="number" value="${prog.held}" min="1">
            </div>
            <div class="form-group">
              <label class="form-label">Ещё будет пар</label>
              <input class="form-input" id="inp-future" type="number" value="${prog.future}" min="1">
            </div>
          </div>
        </details>
      </div>
      <button class="btn" onclick="runCalc()">Рассчитать</button>
    </div>
    <div id="calc-result"></div>`;
}

function renderCalcManual(el = document.getElementById("page-calc")) {
  el.innerHTML = `
    <div class="page-header">Калькулятор</div>
    <div class="card">
      <div class="card-title">Параметры</div>
      <div style="display:flex;flex-direction:column;gap:10px">
        <div class="form-group">
          <label class="form-label">Текущая посещаемость (%)</label>
          <input class="form-input" id="inp-pct" type="number" value="85" min="0" max="100">
        </div>
        <div class="form-group">
          <label class="form-label">Уже прошло пар</label>
          <input class="form-input" id="inp-held" type="number" value="20" min="1">
        </div>
        <div class="form-group">
          <label class="form-label">Ещё будет пар</label>
          <input class="form-input" id="inp-future" type="number" value="30" min="1">
        </div>
        <div class="form-group">
          <label class="form-label">Планирую пропустить</label>
          <input class="form-input" id="inp-skips" type="number" value="0" min="0">
        </div>
      </div>
    </div>
    <button class="btn" onclick="runCalcManual()">Рассчитать</button>
    <div id="calc-result"></div>`;
}

async function selectCalcSubject(i) {
  haptic("light");
  state.calcSubjectIdx = i;
  renderCalcForm();
  const row = state.brs[i];
  if (!row?.lessons_url) return;
  try {
    const stats = await apiFetch(`/api/brs/lessons?lessons_url=${encodeURIComponent(row.lessons_url)}`);
    const heldEl   = document.getElementById("inp-held");
    const futureEl = document.getElementById("inp-future");
    if (heldEl && stats.total > 0) {
      heldEl.value = stats.total;
      if (futureEl) futureEl.value = estimateSemesterProgress().future;
      const hint = document.getElementById("lessons-hint");
      if (hint) hint.textContent = `Данные из БРС: ${stats.attended} из ${stats.total} посещено`;
    }
  } catch(_) {}
}

async function runCalc() {
  const row = state.brs[state.calcSubjectIdx];
  if (!row) return;
  haptic("medium");
  const held   = parseInt(document.getElementById("inp-held")?.value)   || 20;
  const future = parseInt(document.getElementById("inp-future")?.value) || 30;
  const skips  = parseInt(document.getElementById("inp-skips")?.value)  || 0;
  const res = await apiFetch("/api/calc/attendance", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ current_pct: row.attendance_pct ?? 85, classes_held: held, future_total: future, skips }),
  });
  showCalcResult(res, row.subject, skips);
}

async function runCalcManual() {
  haptic("medium");
  const pct    = parseFloat(document.getElementById("inp-pct")?.value)   || 85;
  const held   = parseInt(document.getElementById("inp-held")?.value)    || 20;
  const future = parseInt(document.getElementById("inp-future")?.value)  || 30;
  const skips  = parseInt(document.getElementById("inp-skips")?.value)   || 0;
  const res = await apiFetch("/api/calc/attendance", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ current_pct: pct, classes_held: held, future_total: future, skips }),
  });
  showCalcResult(res, null, skips);
}

function showCalcResult(res, subject, skips) {
  const change = res.change;
  const cls = change > 0 ? "positive" : change < 0 ? "negative" : "neutral";
  const changeStr = change > 0 ? `+${change}` : `${change}`;
  document.getElementById("calc-result").innerHTML = `
    <div class="result-card">
      <div class="result-title">${subject || "Результат"}</div>
      <div class="result-row"><span class="result-label">Сейчас</span>
        <span class="result-value">${res.current_pct}% · ${res.current_grade}</span></div>
      <div class="result-row"><span class="result-label">После ${skips} пропусков</span>
        <span class="result-value">${res.new_pct}% · ${res.new_grade}</span></div>
      <div class="result-row"><span class="result-label">Изменение</span>
        <span class="result-value ${cls}">${changeStr} балла</span></div>
      <div class="result-row"><span class="result-label">Статус</span>
        <span class="result-value">${res.grade} ${res.description}</span></div>
    </div>`;
  if (change < 0) haptic("warning");
}

// ─── PROFILE ──────────────────────────────────────────────────────────────────
const profileSetup = { step: null, course: null, group: null };

async function renderProfilePage() {
  const el = document.getElementById("page-profile");
  if (profileSetup.step) { renderProfileSetup(el); return; }

  el.innerHTML = `<div class="page-header">Профиль</div><div class="loader"><div class="spinner"></div></div>`;

  if (userId) {
    [state.profile, state.reminderSettings] = await Promise.all([
      apiFetch(`/api/profile/${userId}`).catch(() => null),
      apiFetch(`/api/reminders/${userId}`).catch(() => ({ enabled: false, minutes_before: 15 })),
    ]);
  }

  if (state.profile) {
    const subLabel = ["Вся группа","1 подгруппа","2 подгруппа"][state.profile.subgroup] ?? "—";
    const rs = state.reminderSettings;
    const minsRow = rs.enabled ? `
      <div class="card" style="padding:14px;margin-top:8px">
        <div class="card-title" style="margin-bottom:10px">За сколько минут до пары</div>
        <div style="display:flex;gap:8px" id="mins-chips">
          ${[10,15,30,60].map(m => `
            <div class="mins-chip ${rs.minutes_before===m?'active':''}" data-mins="${m}" onclick="setReminderMinutes(${m})">${m} мин</div>
          `).join('')}
        </div>
      </div>` : '';

    el.innerHTML = `
      <div class="page-header">${userName ? escapeHtml(userName) : 'Профиль'}</div>
      <div class="profile-info">
        <div class="profile-row"><span class="profile-key">Курс</span><span class="profile-val">${state.profile.course}</span></div>
        <div class="profile-row"><span class="profile-key">Группа</span><span class="profile-val">${state.profile.group}</span></div>
        <div class="profile-row"><span class="profile-key">Подгруппа</span><span class="profile-val">${subLabel}</span></div>
      </div>
      <button class="btn btn-secondary" onclick="startProfileSetup()">✏️ Изменить профиль</button>

      <div class="home-section-title" style="margin-top:24px">⏰ Напоминания</div>
      <div class="toggle-row">
        <div>
          <div class="toggle-label">Напоминания о парах</div>
          <div style="font-size:12px;color:var(--hint);margin-top:3px">Бот пришлёт сообщение в Telegram</div>
        </div>
        <div class="toggle ${rs.enabled?'on':''}" id="reminder-toggle" onclick="toggleReminder()"></div>
      </div>
      <div id="mins-section">${minsRow}</div>

      <div class="divider" style="margin:24px 0 16px"></div>
      <div class="home-section-title" style="margin-top:0">❓ FAQ</div>
      <div id="faq-container">${skelSubjectCards(3)}</div>`;
    renderFaqInProfile();
  } else {
    el.innerHTML = `
      <div class="page-header">Профиль</div>
      <div class="empty-state"><div class="empty-icon">👤</div>
      <div class="empty-text">Профиль не настроен</div></div>
      <button class="btn" onclick="startProfileSetup()">Настроить</button>`;
  }
}

// ─── REMINDERS UI ────────────────────────────────────────────────────────────
async function toggleReminder() {
  if (!userId) return;
  haptic("medium");
  state.reminderSettings.enabled = !state.reminderSettings.enabled;
  const toggle = document.getElementById("reminder-toggle");
  if (toggle) toggle.classList.toggle("on", state.reminderSettings.enabled);
  await apiFetch(`/api/reminders/${userId}`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(state.reminderSettings),
  }).catch(() => {});
  // show/hide minutes section
  const sec = document.getElementById("mins-section");
  if (sec) {
    const rs = state.reminderSettings;
    sec.innerHTML = rs.enabled ? `
      <div class="card" style="padding:14px;margin-top:8px">
        <div class="card-title" style="margin-bottom:10px">За сколько минут до пары</div>
        <div style="display:flex;gap:8px" id="mins-chips">
          ${[10,15,30,60].map(m => `
            <div class="mins-chip ${rs.minutes_before===m?'active':''}" data-mins="${m}" onclick="setReminderMinutes(${m})">${m} мин</div>
          `).join('')}
        </div>
      </div>` : '';
  }
}

async function setReminderMinutes(mins) {
  if (!userId) return;
  haptic("light");
  state.reminderSettings.minutes_before = mins;
  document.querySelectorAll(".mins-chip").forEach(c =>
    c.classList.toggle("active", parseInt(c.dataset.mins) === mins)
  );
  await apiFetch(`/api/reminders/${userId}`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(state.reminderSettings),
  }).catch(() => {});
}

// ─── FAQ ─────────────────────────────────────────────────────────────────────
async function renderFaqInProfile() {
  const container = document.getElementById("faq-container");
  if (!container) return;
  if (!state.faqLoaded) {
    try {
      state.faq = await apiFetch("/api/faq");
      state.faqLoaded = true;
    } catch(e) {
      container.innerHTML = `<div style="color:var(--hint);font-size:13px">Не удалось загрузить FAQ</div>`;
      return;
    }
  }
  container.innerHTML = state.faq.map((item, i) => `
    <div class="faq-item" id="faq-${i}">
      <div class="faq-question" onclick="toggleFaq(${i})">
        <span>${escapeHtml(item.question)}</span><span class="faq-arrow">▾</span>
      </div>
      <div class="faq-answer"><div class="faq-answer-inner">${escapeHtml(item.answer)}</div></div>
    </div>`).join("");
}

function toggleFaq(i) { document.getElementById(`faq-${i}`)?.classList.toggle("open"); }

// ─── PROFILE SETUP ────────────────────────────────────────────────────────────
async function startProfileSetup() {
  profileSetup.step = "course";
  profileSetup.course = null;
  profileSetup.group = null;
  const el = document.getElementById("page-profile");
  el.innerHTML = `<div class="page-header">Профиль</div><div class="loader"><div class="spinner"></div>Загружаю группы...</div>`;
  try { state.availableGroups = await apiFetch("/api/schedule/groups"); } catch(_) {}
  renderProfileSetup(el);
}

function renderProfileSetup(el) {
  if (profileSetup.step === "course") {
    const courses = Object.keys(state.availableGroups).length
      ? Object.keys(state.availableGroups).map(Number).sort()
      : [1,2,3,4,5];
    el.innerHTML = `<div class="page-header">Курс</div>
      <div class="select-grid">${courses.map(c =>
        `<div class="select-option ${profileSetup.course===c?'active':''}" onclick="selectCourse(${c})">${c} курс</div>`
      ).join("")}</div>`;
  } else if (profileSetup.step === "group") {
    const groups = (state.availableGroups[String(profileSetup.course)] || []).sort((a,b)=>a-b);
    const list = groups.length ? groups : Array.from({length:20},(_,i)=>i+1);
    el.innerHTML = `<div class="page-header">${profileSetup.course} курс — Группа</div>
      <div class="select-grid">${list.map(g =>
        `<div class="select-option ${profileSetup.group===g?'active':''}" onclick="selectGroup(${g})">${g}</div>`
      ).join("")}</div>
      <button class="btn btn-secondary" style="margin-top:8px" onclick="profileSetup.step='course';renderProfileSetup(document.getElementById('page-profile'))">← Назад</button>`;
  } else if (profileSetup.step === "subgroup") {
    el.innerHTML = `<div class="page-header">Подгруппа</div>
      <div class="select-grid" style="grid-template-columns:1fr 1fr">
        <div class="select-option" onclick="selectSubgroup(1)">1️⃣ Подгр. 1</div>
        <div class="select-option" onclick="selectSubgroup(2)">2️⃣ Подгр. 2</div>
      </div>
      <div class="select-grid" style="grid-template-columns:1fr;margin-top:-4px">
        <div class="select-option" onclick="selectSubgroup(0)">👥 Вся группа</div>
      </div>
      <button class="btn btn-secondary" style="margin-top:4px" onclick="profileSetup.step='group';renderProfileSetup(document.getElementById('page-profile'))">← Назад</button>`;
  }
}

function selectCourse(c) { haptic("light"); profileSetup.course=c; profileSetup.step="group";    renderProfileSetup(document.getElementById("page-profile")); }
function selectGroup(g)  { haptic("light"); profileSetup.group=g;  profileSetup.step="subgroup"; renderProfileSetup(document.getElementById("page-profile")); }

async function selectSubgroup(s) {
  haptic("medium");
  const el = document.getElementById("page-profile");
  el.innerHTML = `<div class="page-header">Профиль</div><div class="loader"><div class="spinner"></div>Сохраняю...</div>`;
  if (userId) {
    await apiFetch(`/api/profile/${userId}`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ course: profileSetup.course, group: profileSetup.group, subgroup: s }),
    }).catch(console.error);
  }
  state.profile = { course: profileSetup.course, group: profileSetup.group, subgroup: s };
  state.schedule = []; state.scheduleLoaded = false;
  profileSetup.step = null;
  renderProfilePage();
}

// ─── Boot ────────────────────────────────────────────────────────────────────
async function boot() {
  if (userId) {
    state.profile = await apiFetch(`/api/profile/${userId}`).catch(() => null);
  }
  currentPageName = null;
  goTo(state.profile ? "home" : "profile");
}

boot();
