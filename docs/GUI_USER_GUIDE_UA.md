# CreatorRadar Research Console — довідник інтерфейсу

Цей документ пояснює всі вкладки та поля вікна CreatorRadar Research Console. Він описує роботу з уже наявним інтерфейсом і не є окремою специфікацією формул.

## 1. Загальний порядок роботи

Для першого історичного експерименту порядок такий:

1. Відкрити вкладку **Experiments topics**.
2. Заповнити тему, режим і часовий період.
3. Перейти на **Settings quota** та перевірити параметри.
4. Натиснути **Estimate cost**.
5. Якщо статус `SAFE`, натиснути **Run experiment**.
6. Дочекатися завершення збору.
7. Відкрити **Analytics known events** і натиснути **Refresh**.
8. Вибрати експеримент і тему.
9. Додати перевірену Known Event та увімкнути її маркер на графіку.

Known Event не потрібно додавати до запуску. Це еталонна мітка для аналізу вже зібраних даних.

## 2. Вкладка Experiments topics

### Внутрішня вкладка Experiment

| Поле | Значення | Що вибирати для першого Historical тесту |
|---|---|---|
| **API profile** | Назва профілю YouTube API. Секретний ключ не показується. | `DEFAULT`, якщо ключ записаний у `.env` як `YOUTUBE_API_KEY_DEFAULT`. |
| **Mode** | Тип дослідження. | `Historical Event Validation` для аналізу минулого періоду. |
| **Topic pool** | Готовий тематичний набір. | `Custom`, якщо вводите власні теми. |
| **Topics (semicolon separated)** | Теми через крапку з комою. Кожна тема створює окремий topic ID. | Наприклад: `GTA VI` або `GTA VI; Minecraft`. Для першого тесту краще одна тема. |
| **Window** | Тип часового вікна: `ROLLING` або `STATIC`. | У Historical режимі автоматично `STATIC`. |
| **Rolling hours** | Довжина рухомого вікна в годинах. | У Historical режимі не використовується. |
| **From** | Початок статичного періоду в UTC. | Дата, з якої шукати опубліковані відео. |
| **To** | Кінець статичного періоду в UTC. | Дата завершення періоду. |
| **Max pages** | Максимальна кількість сторінок YouTube Search API. | `1` для першого тесту; збільшуйте, якщо потрібне ширше покриття. |
| **Page size** | Кількість результатів на одну сторінку, від 1 до 50. | `50` — максимальна сторінка. |
| **Duration (minutes)** | Тривалість продуктового або повторюваного експерименту. | Для Historical зазвичай достатньо `1`. Сам період береться з `From` і `To`. |
| **Discovery interval (minutes)** | Інтервал повторного пошуку. | Для Historical фактично не потрібен, бо виконується один збір. |
| **Counter tracking (minutes)** | Інтервал оновлення лічильників уже знайдених відео. | Для Historical публікаційного аналізу не використовується. |

Для Historical Event Validation найважливіші поля: `API profile`, `Mode`, `Topics`, `From`, `To`, `Max pages`, `Page size`.

### Кнопки внизу

| Кнопка | Дія |
|---|---|
| **Estimate cost** | Розраховує локальну оцінку майбутніх API-викликів. Мережевий збір не запускає. |
| **Run experiment** | Запускає експеримент у фоновому потоці. Після завершення створює папку `experiments/<experiment_id>`. |
| **Stop** | Просить експеримент безпечно зупинитися. Поточний HTTP-запит може завершитися перед зупинкою. |
| **Save template** | Зберігає поточні налаштування експерименту у JSON. |
| **Load template** | Завантажує раніше збережений шаблон. |

### Повідомлення під формою

`SAFE`, `WARNING` або `WOULD EXCEED BUDGET` — це локальна оцінка, а не фактичний стан Google Cloud.

Наприклад:

```text
SAFE • Local estimate: 9 search calls; 9 other units
```

Це означає, що запланований запуск вкладається у налаштований локальний бюджет із резервом. Після `SAFE` можна натискати **Run experiment**.

## 3. Вкладка Settings quota

| Поле | Для чого |
|---|---|
| **Gap formula JSON** | Файл параметрів оригінальної Gap формули. Для стандартної конфігурації залиште `config/formulas/gap_v1.json`. |
| **Trend formula JSON** | Версія YouTube дослідницької Trend формули. `trend_balanced.json` — базовий варіант. |
| **Baseline minimum videos** | Мінімум попередніх відео автора, необхідних Gap для creator baseline. Якщо менше — відео не потрапляє у Gap розрахунок. |
| **Baseline maximum videos** | Верхня межа попередніх відео автора, які читаються для baseline. |
| **Baseline cache TTL (hours)** | Як довго можна використовувати кеш creator baseline без нового оновлення. |
| **Baseline max playlist pages** | Скільки сторінок uploads playlist дозволено читати для creator baseline. |
| **Search calls / Pacific day** | Локальний денний ліміт для дорогих `search.list` викликів. |
| **Other read units / Pacific day** | Локальний бюджет інших read-викликів: videos, channels, playlistItems. |
| **Safety reserve (%)** | Частина бюджету, яку система не використовує. При `10` система працює максимум приблизно з 90% локального ліміту. |
| **Exploration (%)** | Частка Product Simulation, що віддається новим або рідше перевіреним темам. Для Historical не має практичного значення. |
| **Discovery overlap (minutes)** | Перекриття rolling-вікон, щоб не пропустити відео через затримку індексації YouTube. Для STATIC не має значення. |
| **Tracked videos per topic** | Максимальна кількість відео у реєстрі counter tracking. |
| **Max retries** | Максимальна кількість повторів тимчасових API-помилок. Кожна спроба враховується у локальній оцінці. |

Для першого Historical запуску можна залишити стандартні значення. Найбільше впливають `Max pages`, `Page size`, `Search calls`, `Other read units` і `Safety reserve`.

## 4. Вкладка Analytics known events

### Поля вибору

| Поле | Що робить |
|---|---|
| **Experiment** | Список завершених локальних експериментів. Якщо порожній — спочатку запустіть експеримент і натисніть `Refresh`. |
| **Topic** | Теми, які були записані у вибраному експерименті. |
| **Metric** | Перша числова серія для графіка, наприклад `historical: sampled_publication_count`, `trend: ewma` або `gap: gap_score`. |
| **Overlay** | Друга числова серія для порівняння. `None` вимикає другу лінію. |
| **Hours relative to event** | Перемикає вісь з календарного часу на `T-24h`, `T`, `T+24h`. |
| **Show event markers** | Показує вертикальні лінії Known Events. |
| **Event selector** | Вибір конкретної події після її імпорту. |

### Кнопки

| Кнопка | Дія |
|---|---|
| **Refresh** | Перечитує папки `experiments` після нового запуску. |
| **Import events JSON** | Імпортує один або кілька Known Events із JSON-масиву. |
| **Add verified event** | Відкриває текстове поле для ручного введення одного JSON-об’єкта. |
| **Export selected series** | Експортує вибрану серію у JSON або CSV. |

### Формат Known Events JSON

Файл повинен містити масив, а не один об’єкт:

```json
[
  {
    "topic_id": "gta_vi_12345678",
    "event_name": "Official announcement",
    "event_type": "announcement",
    "event_time": "2026-08-15T16:00:00Z",
    "description": "Verified event description",
    "source_url": "https://www.rockstargames.com/",
    "source_type": "official",
    "verified_at": "2026-09-18T10:00:00Z"
  }
]
```

Значення `topic_id` у прикладі умовне. Справжнє значення можна знайти після запуску у:

```text
experiments/<experiment_id>/topics.json
```

`event_time` та `verified_at` повинні мати timezone, наприклад суфікс `Z` для UTC. `source_url` має бути посиланням на джерело, яке можна перевірити.

### Що означає порожній графік

Перевірте послідовно:

1. Натиснуто **Refresh**.
2. Обрано `Experiment`.
3. Обрано `Topic`.
4. У `Metric` вибрано числову серію.
5. Період справді містить знайдені відео.
6. У `Run log` немає `PARTIAL`, `FAILED` або `QUOTA_STOPPED`.

Для першого Historical запуску найкраще вибрати `historical: sampled_publication_count`.

## 5. Вкладка Replay comparison

| Поле або кнопка | Значення |
|---|---|
| **Experiment** | Джерело, з якого беруться вже збережені raw observations. |
| **Trend formulas** | Один або кілька JSON-файлів формул для порівняння. Можна вибрати кілька через Ctrl-click. |
| **Replay / compare selected formulas** | Перераховує метрики без API-викликів і створює нові result workspaces. |
| **Comparison chart** | Порівнює EWMA першої теми у вибраних replay-експериментах. |

Replay не збирає нові відео, не витрачає YouTube quota і не змінює raw files.

## 6. Вкладка API quota

Тут відображається локальна оцінка:

- вибраний API profile;
- використані search calls;
- використані інші read units;
- налаштовані ліміти;
- safety reserve;
- оцінена залишкова місткість.

Це не повна статистика Google Cloud. Якщо той самий API project використовується іншими програмами, вони не будуть видимі у локальному лічильнику.

## 7. Вкладка Run log

Тут відображаються етапи запуску:

- створення experiment;
- початок і завершення collection;
- topic selection;
- Gap enrichment;
- статус `COMPLETE`, `PARTIAL`, `FAILED`, `CANCELLED` або `QUOTA_STOPPED`;
- локальна інформація про quota.

Технічний журнал також зберігається у `data/research.log`. API ключі туди не записуються.

## 8. Рекомендована перша конфігурація

Для перевірки Historical pipeline:

```text
API profile: DEFAULT
Mode: Historical Event Validation
Topic pool: Custom
Topics: одна тема
Window: STATIC
From/To: обраний минулий період
Max pages: 1
Page size: 50
Duration: 1 minute
```

Після завершення: `Analytics → Refresh → Experiment → Topic → historical: sampled_publication_count`.
Known Event додавайте після цього, коли вже видно, що експеримент справді створив дані.
