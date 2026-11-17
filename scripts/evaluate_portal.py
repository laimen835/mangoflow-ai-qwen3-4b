"""50 explicit local acceptance cases. Not a general LLM benchmark.

Requires a running API, PostgreSQL, Ollama, and imported NCSA source.
Writes logs/acceptance-results.json with failed cases and actual responses.
"""
import json
import sys
import time
from pathlib import Path

import httpx


def main():
    results = []
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=240) as client:
        def case(name, operation, check):
            started = time.monotonic()
            try:
                value = operation()
                passed = bool(check(value))
                results.append({"case": name, "passed": passed, "response": value,
                                "seconds": round(time.monotonic() - started, 3)})
            except Exception as exc:
                results.append({"case": name, "passed": False, "error": str(exc)})
            print(f"{'PASS' if results[-1]['passed'] else 'FAIL'} {name}", flush=True)

        def chat(message=None, **extra):
            response = client.post("/api/v1/chat", json={**({"message": message} if message else {}), **extra})
            response.raise_for_status()
            return response.json()

        def get(path):
            r = client.get("/api/v1/portal/" + path)
            r.raise_for_status()
            return r.json()

        for q in ["Контакты колледжа", "Как связаться с колледжем?", "Какой адрес колледжа?",
                  "Где находится колледж?", "Какой телефон СПК?", "Электронная почта СПК",
                  "Нужен телефон колледжа", "Как написать на почту колледжа?",
                  "Подскажи адрес СПК", "Как найти контакты СПК?"]:
            case(q, lambda q=q: chat(q), lambda v: "Ленина 152" in v["answer"] and "spk@ncsa.ru" in v["answer"])
        for q in ["Кто директор колледжа?", "Как зовут директора СПК?", "ФИО директора колледжа", "Директор СПК СКГА"]:
            case(q, lambda q=q: chat(q), lambda v: "Гурина Ирина Алексеевна" in v["answer"] and bool(v["sources"]))
        for q in ["Сейчас числитель или знаменатель?", "Какая сейчас неделя?", "Эта неделя — числитель?",
                  "Сейчас знаменатель?", "Как определить числитель в СПК?"]:
            case(q, lambda q=q: chat(q), lambda v: "не подтверждён" in v["answer"])
        for surname in ["Черкашина", "Котлярова", "Мамхягов", "Иванов", "Черкашна"]:
            case("Преподаватель " + surname, lambda surname=surname: chat("Работает ли преподаватель " + surname + " в СПК?"),
                 lambda v: bool(v["sources"]) and any(w in v["answer"] for w in ["указаны", "имели в виду"]))
        for name in ["Никогданевстречавшийся", "Космонавтов Плутоний"]:
            case("Неизвестный преподаватель " + name, lambda name=name: chat("Работает ли преподаватель " + name + "?"),
                 lambda v: "не доказывает" in v["answer"])
        for department in ["информационных технологий", "технологического отделения", "управления и права",
                           "проектирования и приборостроения", "СПК"]:
            case("Расписание " + department, lambda department=department: chat("Где найти расписание " + department + "?"),
                 lambda v: bool(v["sources"]) and all("/upload/" in s["url"] for s in v["sources"]))
        for q in ["Какие завтра пары?", "Какие сегодня занятия?"]:
            case(q, lambda q=q: chat(q, group="09.02.07-251"), lambda v: "09.02.07-251" in v["answer"] and "не подтверждён" in v["answer"])
        for q in ["Где найти информацию о поступлении в СПК?", "Какие документы нужны для поступления в СПК?",
                  "Где находится приёмная комиссия СПК?"]:
            case(q, lambda q=q: chat(q), lambda v: any("postupayushchim-v-spk" in (s.get("url") or "") for s in v["sources"]))
        for q in ["Какая погода на Марсе?", "Сколько спутников у Нептуна?", "Назови столицу Австралии", "Напиши рецепт тирамису"]:
            case("Вне базы: " + q, lambda q=q: chat(q), lambda v: "не наш" in v["answer"].lower())
        for q in ["Черкашина", "Котлярова", "Иванов", "Черкашна"]:
            case("Поиск справочника: " + q, lambda q=q: get("teachers?q=" + q), lambda v: len(v["items"]) > 0)
        for group in ["09.02.07-251", "40.02.04-251"]:
            case("Фильтр группы: " + group, lambda group=group: get("schedule?group=" + group),
                 lambda v: len(v["items"]) == 1)
        case("Статус базы и дата проверки", lambda: get("overview"), lambda v: v["website_documents"] >= 17 and v["teachers"] >= 90 and v["checked_at"])
        case("Источники и локальные документы", lambda: get("sources"), lambda v: any(i["is_test"] for i in v["items"]) and any(i["extraction"] == "ocr" for i in v["items"]))

        def feedback():
            answer = chat(intent="contacts")
            r = client.post(f"/api/v1/portal/messages/{answer['message_id']}/feedback",
                json={"session_id": answer["session_id"], "rating": 1})
            r.raise_for_status()
            return r.json()
        case("Сохранение оценки", feedback, lambda v: v["saved"])

        def streaming():
            events = []
            with client.stream("POST", "/api/v1/chat/stream", json={"message": "Что известно об истории колледжа СПК СКГА?"}) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if line:
                        events.append(json.loads(line))
            return {"delta_events": sum(e["type"] == "delta" for e in events), "last_event": events[-1]}
        case("Постепенный вывод ответа Ollama", streaming, lambda v: v["delta_events"] > 1 and v["last_event"]["type"] == "done")
    report = {"type": "local_acceptance_checks", "cases": len(results),
              "passed": sum(r["passed"] for r in results), "results": results,
              "limitation": "Explicit acceptance checks, not a comprehensive semantic accuracy benchmark."}
    Path("logs/acceptance-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{report['passed']}/{len(results)} passed", flush=True)
    if report["passed"] != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
