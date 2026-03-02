---
trigger: model_decision
description: Схема бази даних для графа Grynya_v2.0
globs: **/*.py, **/*.md, **/*.json
---

# Схема Графа Пам'яті "Grynya_v2.0"

Цей документ описує поточний стан схеми графа `Grynya_v2.0` для бази даних FalkorDB. Знімок зроблено 2026-03-01. Схема перебуває в активному рефакторингу.

## Вузли (Nodes)

Лише типи, що мають реальні екземпляри у графі.

| Тип вузла | Відомі атрибути | Екземпляри (`id` → `name` : `description`) |
| :--- | :--- | :--- |
| `:Year` | `id`, `name`, `value` | `year_2026` → `2026` |
| `:Day` | `id`, `name`, `date` | `d_2026_02_28` → `d_28` |
| `:Conceptions` | `id`, `name`, `description` | `id_agents` → `AGENTS` : "Перелік агентів що належать до екосистеми" |
| | | `id_agents_state` → `AGENTS_STATE` : "Стан агента що завантажується автоматично через МСР" |
| `:Agents` | `id`, `name`, `description` | `id_cursa4` → `Cursa4` : "Агент вбудований у Cursor IDE" |
| | | `id_clim` → `Clim` : "Нейронне ядро у LLM_provider_MCP" |
| `:SubConceptions` | `id`, `name`, `description` | `id_context` → `CONTEXT` : "Структурований опис останніх подій" |
| | | `id_tasks` → `TASKS` : "Перелік пріоритетних завдань" |
| | | `id_rules` → `RULES` : "Системні правила та директиви" |
| | | `id_pole` → `ROLE` : "Роль та Особистість" |
| | | `id_skills` → `SKILLS` : "Перелік навичок агентів" |
| `:System` | `id`, `type`, `name`, `content` | `sys_role_grynya` → `Системна Роль (Гриня)` (type: `role`) |
| | | `sys_rule_lang` → `Системні правила (Мовні Директиви)` (type: `rules`) |
| | | `sys_rule_code` → `Системні правила (Код)` (type: `rules`) |
| | | `sys_rule_limits` → `Системні правила (Межі Відповідальності)` (type: `rules`) |
| | | `sys_tasks_1` → `System Tasks` (type: `tasks`) |
| `:State` | `id`, `name` | `state_test_1` → `12:20` |

## Зв'язки (Relationships)

Лише ті, що мають реальні екземпляри у графі.

| Тип зв'язку | Від | До | Атрибути | Екземпляри |
| :--- | :--- | :--- | :--- | :--- |
| `[:MONTH]` | `:Year` | `:Day` | `number` (integer) | `year_2026` → `d_2026_02_28` (number: 2) |
| `[:REFERS_TO]` | `:Conceptions` | `:Agents` | `id` | `id_agents` → `id_cursa4` |
| | | | | `id_agents` → `id_clim` |
| `[:BLOCK_1]` | `:Conceptions` | `:SubConceptions` | — | `id_agents_state` → `id_pole` |
| `[:BLOCK_2]` | `:Conceptions` | `:SubConceptions` | `id` | `id_agents_state` → `id_rules` |
| `[:BLOCK_3]` | `:Conceptions` | `:SubConceptions` | — | `id_agents_state` → `id_tasks` |
| `[:BLOCK_4]` | `:Conceptions` | `:SubConceptions` | `id` | `id_agents_state` → `id_context` |
| `[:BLOCK_5]` | `:Conceptions` | `:SubConceptions` | `id` | `id_agents_state` → `id_skills` |
| `[:CONTAINS]` | `:SubConceptions` | `:System` | — | `id_pole` → `sys_role_grynya` |
| | | | | `id_rules` → `sys_rule_lang` |
| | | | | `id_rules` → `sys_rule_code` |
| | | | | `id_rules` → `sys_rule_limits` |
| | | | | `id_tasks` → `sys_tasks_1` |
| `[:BLOCK_1]` | `:State` | `:System` | — | `state_test_1` → `sys_role_grynya` |
| `[:BLOCK_2]` | `:State` | `:System` | — | `state_test_1` → `sys_rule_lang` |
| `[:BLOCK_2]` | `:State` | `:System` | — | `state_test_1` → `sys_rule_code` |
| `[:BLOCK_2]` | `:State` | `:System` | — | `state_test_1` → `sys_rule_limits` |
| `[:BLOCK_3]` | `:State` | `:System` | — | `state_test_1` → `sys_tasks_1` |
