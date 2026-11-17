# Воспроизведение проверки 17.11.2026

Полный отчёт: `docs/APPLICANT-MODEL-COMPARISON-20261117.md`.
Код приложения и веса не изменены. Сохранённые здесь вопросы уже просмотрены
и пригодны для регрессий, не для нового независимого контроля.

Скрипты относятся к выделенной БД `rag_admissions_eval_20261117` на 55433.
Для нового независимого запуска выбрать новый результат/БД и явно адаптировать
защитный allowlist; не отключать guard. Для повторения регрессии на этой БД
используются новые сессии; пользовательские базы не затрагиваются. Запускать
из корня checkout, с приватной `.env.local` и следующими переменными процесса:

```powershell
$env:DATABASE_URL='postgresql+asyncpg://codex_deep@127.0.0.1:55433/rag_admissions_eval_20261117'
$env:PYTHONUTF8='1'
$env:PYTHONPATH='D:\Codex-RAG-runtime\deep-knowledge-20261117\optional;D:\Codex-RAG-runtime\qwen35-9b-20261117\app'
$taskPython='C:\Users\islam\Documents\Codex\2026-10-29\https-github-com-technasher-rag-https-2\RAG\.venv\Scripts\python.exe'
& $taskPython tests/eval/applicants_20261117/run_applicants.py --model 4b
# Только после завершения 4B:
& $taskPython tests/eval/applicants_20261117/run_applicants.py --model 9b
& $taskPython tests/eval/applicants_20261117/prepare_review.py
& $taskPython tests/eval/applicants_20261117/audit_evidence.py
```

Модели должны быть установлены в работающих Ollama 11534 и 11535. Runner
освобождает только кэш противоположной LLM, не останавливает серверы. Выполняет
прогрев вне основной статистики и проверяет сохранность корпуса/кода.
Результат не перезаписывается. Перед повтором использовать новую папку с
материалами и новыми выходными файлами, сохраняя предыдущие результаты.

`prepare_review.py` скрывает имена моделей. Оценить все пары по замороженным
критериям, записать `judgments-blinded.json`, зафиксировать SHA256, затем
использовать `summarize.py` для раскрытия и CSV. Старые ручные оценки нельзя
переносить на новые ответы автоматически. `check_real_dialogues.py` — отдельная
диагностика, добавленная после просмотра основного контроля; не смешивать её
с основной оценкой. Выходные ответы/контексты/журналы игнорируются Git.
