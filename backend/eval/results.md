# TaskPilot — Agent Evaluation Results

Ran **24** tasks (research, deep-research, action, mixed, robustness).

| Metric | Score |
|--------|------:|
| Task success (LLM-judged) | 54.2% |
| Tool-selection accuracy | 93.8% |
| Avg tool calls / task | 2.0 |
| Tasks that hit an error event | 0.0% |
| Tasks that triggered approval gate | 37.5% |

## Per-task

| ID | Category | Tools used | Tool-sel | Success |
|----|----------|-----------|---------:|:-------:|
| t01 | research | web_search | 100% | ❌ |
| t02 | research | web_search | 100% | ❌ |
| t03 | research | web_search | 100% | ❌ |
| t04 | research | web_search, read_url | 100% | ❌ |
| t05 | research | web_search | 100% | ✅ |
| t06 | research-deep | web_search | 50% | ❌ |
| t07 | research-deep | web_search, read_url | 100% | ❌ |
| t08 | research-deep | web_search, read_url | 100% | ❌ |
| t09 | action | add_task | 100% | ✅ |
| t10 | action | web_search, add_task | 100% | ❌ |
| t11 | action | list_tasks, add_task | 100% | ✅ |
| t12 | action | list_tasks | 100% | ✅ |
| t13 | mixed | web_search, add_task | 100% | ❌ |
| t14 | mixed | web_search, web_search, read_url, add_task | 100% | ✅ |
| t15 | research | — | 0% | ✅ |
| t16 | research | web_search | 100% | ❌ |
| t17 | research-deep | web_search, read_url, web_search | 100% | ✅ |
| t18 | research | web_search | 100% | ✅ |
| t19 | action | add_task, list_tasks | 100% | ✅ |
| t20 | robustness | read_url, web_search | 100% | ✅ |
| t21 | research | web_search | 100% | ✅ |
| t22 | mixed | web_search, web_search, read_url, read_url, add_task | 100% | ✅ |
| t23 | robustness | list_tasks, complete_task, complete_task, complete_task, complete_task, complete_task, complete_task, complete_task | 100% | ❌ |
| t24 | robustness | add_task, complete_task | 100% | ✅ |