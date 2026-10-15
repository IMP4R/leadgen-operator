"""Live test of the dialog engine: 4 scripted lead scenarios."""
from app.dialog_engine import DialogEngine

CASES = [
    ("warm_lead", [], "Здравствуйте. Да, вопрос найма ещё актуален. А вы откуда пишете?"),
    ("price_question", [
        {"role": "assistant", "text": "Приветствую! У вас с наймом как обстоит — тоже сложности с откликами?"},
        {"role": "user", "text": "Есть такое, отклики слабые"},
    ], "Сколько у вас стоит подбор?"),
    ("not_target", [
        {"role": "assistant", "text": "Подскажите, закрываете вакансии своими силами или привлекаете кого-то со стороны?"},
        {"role": "user", "text": "Сами пока"},
    ], "Нам надо 40 официантов на вахту за Урал"),
    ("expensive", [
        {"role": "assistant", "text": "От 35 тысяч рублей за лучшего специалиста под Ваши требования. Можете изучить наше КП."},
        {"role": "user", "text": "Что входит?"},
    ], "Дорого у вас, честно говоря"),
]

def main():
    eng = DialogEngine()
    for name, history, incoming in CASES:
        print("=" * 20, name)
        print("LEAD:", incoming)
        try:
            r = eng.suggest(history, incoming)
            for i, v in enumerate(r["variants"], 1):
                print("  V%d: %s" % (i, v))
            print("  QUAL:", r["qualification"])
        except Exception as e:
            print("  ERROR:", type(e).__name__, str(e)[:300])

if __name__ == "__main__":
    main()
