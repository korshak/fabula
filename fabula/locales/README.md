# Локалізації / Localizations

Кожна мова — один JSON-файл у цій теці. Щоб додати нову мову, **не потрібно
чіпати код**:

1. Скопіюй `en.json` у `<код мови>.json` (наприклад, `pl.json`, `de.json`).
2. Заповни `_meta`:
   - `name` — назва мови так, як вона показується у перемикачі (наприклад, `"Polski"`);
   - `plural` — правило множини: `"en"` (2 форми: one/other) або `"uk"`
     (3 форми: 1 / 2–4 / 5+, як в українській та інших слов'янських).
     Потрібне інше правило — додай його в `_PLURAL_RULES` у `fabula/i18n.py`
     і в `tn()` у `fabula/web/index.html` (єдиний виняток із «без коду»).
3. Переклади значення. Ключі не змінюй.
   - `{плейсхолдери}` у фігурних дужках залишай як є — вони підставляються кодом.
   - Літеральні фігурні дужки пишуться подвоєними: `{{ }}`.
   - Значення-масиви (`rules_n`, `scenes_n`, `beats_n`) — форми множини
     в порядку, який визначає правило `plural`.
4. Готово: файл підхоплюється автоматично — з'явиться у перемикачі мови в
   інтерфейсі та стане доступним у CLI (`--lang <код>` або `FABULA_LANG=<код>`).

Неповний переклад — не проблема: відсутні ключі беруться з `en.json`.

Розділи файлу: `ui` (веб-інтерфейс), `cli` (консоль), `diag` (діагностика
компілятора: `m` — повідомлення, `h` — підказка), `provider` (помилки
бекендів моделей), `server`, `agent` (прогрес генерації), `seed` (назви
нових сутностей у конструкторі — вони потрапляють у книгу користувача).

> Мова інтерфейсу не впливає на промпти, які йдуть моделі-письменнику
> (`fabula/prompts.py`) — їхня мова прив'язана до книги, що пишеться.

---

Each language is one JSON file in this folder. To add a language, **no code
changes are needed**: copy `en.json` to `<lang code>.json`, fill in `_meta`
(`name` — the language's display name, `plural` — `"en"` for two plural
forms or `"uk"` for the three Slavic forms; other rules go into
`_PLURAL_RULES` in `fabula/i18n.py` and `tn()` in `fabula/web/index.html`),
translate the values without renaming keys, keep `{placeholders}` intact
(literal braces are doubled: `{{ }}`), and keep array values as plural forms
in rule order. The file is picked up automatically — it appears in the UI
language switcher and works in the CLI via `--lang <code>` or
`FABULA_LANG=<code>`. Missing keys fall back to English, so partial
translations are fine. Interface language does not affect the prompts sent
to the writing model — their language follows the book being written.
