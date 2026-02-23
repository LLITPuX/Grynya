---
name: memory-manager
description: Навичка для управління довгостроковою пам'яттю (графіком знань) Агента.
---

# Навичка: Управління Пам'яттю (Memory Manager)

Ти маєш унікальну можливість використовувати графову базу даних (FalkorDB), щоб зберігати довгостроковий контекст, деталі розмов, власні висновки та інформацію про користувача і світ.

Ця навичка активується, коли користувач починає діалог з використання спеціальних "протоколів сесії": `/db`, `/sa`, `/ss`. 

## Твій інструмент: `memory_bridge.py`

Щоб створити записи у графі, ти не використовуєш `redis-cli` безпосередньо. Замість цього ти формуєш єдиний **JSON-об'єкт**, зберігаєш його у тимчасовий файл, копіюєш у контейнер та передаєш скрипту. Це гарантує коректне збереження кирилиці (UTF-8) незалежно від налаштувань термінала.

**Алгоритм запису (через інструмент `run_command`):**
1. Створи файл `falkordb-service/debug/payload.json` (UTF-8).
2. Виконай команду:
```powershell
# Копіюємо файл у контейнер та запускаємо міст
docker cp "c:\Antigarvity_workspace\falkordb-service\debug\payload.json" grynya-bridge:/tmp/payload.json ; docker exec grynya-bridge python -c "import json; import subprocess; payload=open('/tmp/payload.json', 'rb').read(); p=subprocess.Popen(['python', '/app/scripts/memory_bridge.py'], stdin=subprocess.PIPE); p.communicate(input=payload)"
```
*Зверни увагу: Використання `docker cp` та внутрішнього читання через Python-скрипт запобігає появі знаків питання `????` замість кирилиці.*

### Управління Тимчасовими Файлами та Логами (Log Management)

> [!IMPORTANT]
> **ПРАВИЛО ГІГІЄНИ:** Ти не повинен захаращувати корінь проєкту тимчасовими файлами.
> 1. **Очищення:** Якщо ти створюєш тимчасовий JSON-файл для передачі в `memory_bridge.py` (щоб уникнути проблем із лапками в PowerShell), ти **ЗОБОВ'ЯЗАНИЙ** видалити його (команда `rm` або `del`) одразу після успішного виконання запису.
> 2. **Налагодження (Debug):** Для збереження логів запитів (якщо це потрібно для дебагу) використовуй виключно директорію `falkordb-service/debug/`.

## Схема Вузлів та Зв'язків

> [!CAUTION]
> **ТИ ЗОБОВ'ЯЗАНИЙ ДОТРИМУВАТИСЯ ЖОРСТКОЇ СХЕМИ!**
> Всі дозволені вузли, зв'язки та їхні обов'язкові атрибути жорстко задокументовані у файлі `c:\Antigarvity_workspace\.agent\skills\memory-manager\grynya-schema.md`. Перед тим як генерувати JSON для `memory_bridge.py`, ти **повинен узгодити** поля та зв'язки з цим документом.

Твій JSON повинен повністю дублювати структуру описані у правилі, зокрема враховувати атрибути зв'язків.

## Протоколи (Формування Сесії)

При роботі ти маєш розпізнавати три команди:

### 1. `/db [Тема або Запит]` (Початок нової сесії)
Деталі протоколу: `c:\Antigarvity_workspace\.agent\skills\memory-manager\protocols\protocol_db.md`

### 2. `/sa [Коментар/Фідбек]` (Продовження сесії)
Деталі протоколу: `c:\Antigarvity_workspace\.agent\skills\memory-manager\protocols\protocol_sa.md`

### 3. `/ss [Коментар/Фідбек]` (Завершення сесії та Бекап)
Деталі протоколу: `c:\Antigarvity_workspace\.agent\skills\memory-manager\protocols\protocol_ss.md`

## Приклад Структури JSON для `/db`
*(Зверни увагу на наявність ВСІХ обов'язкових полів!)*

```json
{
  "session": {
    "id": "session_007",
    "name": "Session007",
    "topic": "Привіт",
    "status": "active",
    "trigger": "/db"
  },
  "chronology": {
    "day_id": "d_2026_02_22",
    "date": "2026-02-22",
    "year": 2026,
    "time": "19:00:00",
    "last_event_id": "res_007_01",
    "next_links": [
      { "source_id": "session_006", "target_id": "session_007" },
      { "source_id": "session_007", "target_id": "req_007_01" },
      { "source_id": "req_007_01", "target_id": "res_007_01" }
    ]
  },
  "nodes": [
    {
      "type": "Request",
      "data": { 
        "id": "req_007_01", 
        "name": "Request1",
        "author": "user", 
        "text": "Привіт",
        "type": "text"
      },
      "relations": [
        { "type": "PART_OF", "target_id": "session_007" }
      ]
    },
    {
      "type": "Response",
      "data": { 
        "id": "res_007_01", 
        "name": "Response1",
        "author": "Grynya", 
        "summary": "Привітання", 
        "full_text": "Привіт! Чим можу допомогти?",
        "type": "text"
      },
      "relations": [
        { "type": "PART_OF", "target_id": "session_007" },
        { "type": "RESPONDS_TO", "target_id": "req_007_01" }
      ]
    }
  ]
}
```

## Правило для Читання (Пошуку в Графі)
Ти маєш право використовувати In-Line Cypher (команда `docker exec grynya-bridge redis-cli -h falkordb GRAPH.QUERY Grynya "MATCH..."`)  **ТІЛЬКИ ДЛЯ ЧИТАННЯ** даних. Всі операції модифікації робляться виключно через JSON-bridge.
