"""Reply policy for real lead dialogs (TG + YetReply).

Два правила:

1. Тактика двух отказов — отказ не заканчивает диалог сразу. После отказа
   делаем до REENGAGE_ATTEMPTS мягких попыток вернуть интерес и только потом
   вежливо закрываем диалог.
2. Закрытый диалог больше не отвечает автоматически. Входящие сохраняются,
   но ответ уходит только при реальном интересе. Это убирает циклы вида
   "Всего доброго!" на каждое сообщение.

Функции чистые (без БД и сети): их используют tg_listener, yr_listener и панель.
"""
import re

# Сколько раз после отказа ещё пробуем привлечь лида.
REENGAGE_ATTEMPTS = 2

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+", re.UNICODE)

_ABUSE_MARKERS = (
    "ебал", "ебат", "шлюх", "зареж", "зарез", "убью", "убить",
    "сдох", "мраз", "тварь", "пошел нах", "пошел нах", "сука",
)

# Явный отказ — его отрабатываем по тактике двух отказов.
_REFUSAL_MARKERS = (
    "не интересно", "неинтересно", "не интересует", "не интересны",
    "не актуально", "неактуально", "не актуален", "не актуальна",
    "не нужно", "не нужны", "не нужен", "не надо", "не требуется",
    "нет спасибо", "спасибо нет", "спасибо не надо",
    "не рассматриваем", "не планируем", "не ищем", "никого не ищем",
    "уже нашли", "уже закрыли", "вопрос закрыт", "закрыли вопрос",
    "нет необходимости", "нет потребности", "нет запроса",
    "не будем", "не хотим", "отказываемся", "откажусь",
    "справляемся сами", "своими силами",
    "не пишите", "больше не пишите", "не беспокойте", "отстаньте",
    "отпишитесь", "удалите мой", "уберите мой",
)

_SHORT_REFUSALS = ("нет", "неа", "нету", "нетт", "не надо", "нет спасибо")

# Настоящий интерес — только он будит закрытый диалог.
_INTEREST_MARKERS = (
    "ваканси", "подбор", "подобрать", "найм", "нанять", "нанима",
    "сотрудник", "персонал", "кандидат", "рекрут", "кадр", "специалист",
    "позици", "штат", "hr", "резюм",
    "стоимост", "цена", "цены", "сколько стоит", "прайс", "тариф",
    "условия", "договор", "коммерческое", "презентац", "кп",
    "созвон", "созвониться", "созвонимся", "позвонить", "звонок", "встреч",
    "расскажите", "подробнее", "отправьте", "скиньте", "пришлите",
    "интересно", "актуально", "давайте", "готовы", "хотим",
    "нужен человек", "ищем", "требуется", "требуются", "закрыть ваканс",
    "как работаете", "как это работает", "что предлагаете",
)

# Закрываем сразу, без попыток привлечь.
_STOP_NOW_REASONS = (
    "инжект", "спам", "агресс", "угроз", "оскорб", "бессмыслен",
    "тролл", "массов", "вахт", "общепит", "официан", "барист",
    "не наш профиль", "нецелев", "не целев",
)


def _normalize(text):
    text = str(text or "").casefold().replace("ё", "е")
    text = _PUNCT_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def looks_abusive(text):
    normalized = _normalize(text)
    return any(_normalize(marker) in normalized for marker in _ABUSE_MARKERS)


def abuse_fallback():
    return {
        "variants": ["Понял, продолжать диалог не будем. Всего доброго."],
        "qualification": {
            "status": "not_target",
            "reason": "агрессивное или угрожающее сообщение",
            "next_step": "stop",
        },
    }


def looks_refusal(text):
    """Явный отказ лида (не возражение)."""
    normalized = _normalize(text)
    if not normalized:
        return False
    if normalized in _SHORT_REFUSALS:
        return True
    return any(_normalize(marker) in normalized for marker in _REFUSAL_MARKERS)


def looks_interested(text):
    """В сообщении есть реальный интерес к теме подбора."""
    normalized = _normalize(text)
    if not normalized or looks_refusal(text) or looks_abusive(text):
        return False
    return any(_normalize(marker) in normalized for marker in _INTEREST_MARKERS)


def gate_incoming(lead, text):
    """Нужно ли вообще отвечать на это входящее сообщение."""
    lead = dict(lead or {})
    if not int(lead.get("dialog_closed") or 0):
        return {"reply": True, "reopen": False, "reason": "active"}
    if looks_interested(text):
        return {"reply": True, "reopen": True, "reason": "interest_after_close"}
    return {"reply": False, "reopen": False, "reason": "closed_no_interest"}


def classify(text, qual=None, abusive=False):
    """normal | refusal | stop_now."""
    if abusive or looks_abusive(text):
        return "stop_now"
    qual = qual or {}
    reason = _normalize(qual.get("reason"))
    status = str(qual.get("status") or "").strip().lower()
    next_step = str(qual.get("next_step") or "").strip().lower()
    if any(_normalize(marker) in reason for marker in _STOP_NOW_REASONS):
        return "stop_now"
    if looks_refusal(text):
        return "refusal"
    if next_step == "stop" or status == "not_target":
        return "refusal"
    return "normal"


def drop_repeats(variants, last_out):
    """Убрать варианты, дословно повторяющие прошлое исходящее."""
    last = _normalize(last_out)
    fresh = []
    for variant in variants or []:
        value = str(variant or "").strip()
        if not value:
            continue
        if last and _normalize(value) == last:
            continue
        fresh.append(value)
    return fresh


def _plan(action, text=None, refusals=0, closed=False, reason=None,
          kind="normal", attempt=0, is_last=False):
    return {
        "action": action,
        "kind": kind,
        "text": text,
        "refusals": int(refusals or 0),
        "closed": bool(closed),
        "closed_reason": reason,
        "attempt": int(attempt or 0),
        "is_last": bool(is_last),
    }


def plan_reply(text, qual, variants, refusals=0, last_out="", abusive=False):
    """Что делать после того, как модель вернула варианты."""
    kind = classify(text, qual, abusive=abusive)
    fresh = drop_repeats(variants, last_out)
    refusals = int(refusals or 0)

    if kind == "stop_now":
        return _plan(
            "close", text=fresh[0] if fresh else None, refusals=refusals,
            closed=True, reason=(qual or {}).get("reason") or "нецелевой лид",
            kind=kind, is_last=True,
        )

    if kind == "refusal":
        attempt = refusals + 1
        if attempt <= REENGAGE_ATTEMPTS:
            return _plan(
                "reengage", refusals=attempt, kind=kind, attempt=attempt,
                is_last=attempt >= REENGAGE_ATTEMPTS,
            )
        return _plan(
            "close", text=fresh[0] if fresh else None, refusals=attempt,
            closed=True,
            reason="отказ после %d попыток вернуть интерес" % REENGAGE_ATTEMPTS,
            kind=kind, attempt=attempt, is_last=True,
        )

    if not fresh:
        return _plan(
            "close", refusals=refusals, closed=True,
            reason="модель повторяет прошлое сообщение", kind="loop",
            is_last=True,
        )

    return _plan("send", text=fresh[0], refusals=refusals, kind=kind)


def finalize_reengagement(plan, variants, last_out=""):
    """Завершить попытку вернуть интерес после второй генерации."""
    fresh = drop_repeats(variants, last_out)
    plan = dict(plan)
    if not fresh:
        plan.update(action="close", text=None, closed=True,
                    closed_reason="не удалось сформулировать новую попытку")
        return plan
    plan.update(action="send", text=fresh[0])
    return plan


def state_label(lead):
    """Короткая подпись для панели."""
    lead = dict(lead or {})
    refusals = int(lead.get("refusals") or 0)
    if int(lead.get("dialog_closed") or 0):
        return "Закрыт: %s" % (lead.get("closed_reason") or "диалог завершён")
    if refusals:
        return "Отказов: %d из %d" % (refusals, REENGAGE_ATTEMPTS)
    return ""


# --- Качество попыток вернуть интерес ---
# Вопросы про неопределённое будущее бессмысленны: оператор не знает, когда
# ситуация изменится, и никто по таким обещаниям не возвращается.
_WEAK_REENGAGE_MARKERS = (
    "можно будет вам написать", "можно будет написать", "можно вам написать",
    "можно будет к вам вернуться", "можно к вам вернуться",
    "можно будет вернуться", "можно вернуться", "разрешите написать",
    "если ситуация изменится", "если вдруг ситуация", "если что то изменится",
    "если понадобится", "если вдруг понадобится", "если вдруг потребуется",
    "если станет актуально", "если будет актуально", "если вдруг станет актуально",
    "когда появится потребность", "когда возникнет потребность",
    "когда появится задача", "когда возникнет задача", "когда будет актуально",
    "в будущем", "на будущее", "буду на связи", "будем на связи",
    "напишу позже", "вернусь позже", "свяжусь позже",
    "не буду отвлекать", "не буду больше отвлекать", "не буду беспокоить",
)


def is_weak_reengagement(text):
    """Попытка вернуть интерес не должна просить разрешения написать когда-то потом."""
    value = str(text or "").strip()
    if not value:
        return True
    if value.count("?") != 1:
        return True
    normalized = _normalize(value)
    return any(_normalize(marker) in normalized for marker in _WEAK_REENGAGE_MARKERS)


def filter_reengagement(variants, last_out=""):
    """Оставить только варианты с живым вопросом про текущую ситуацию."""
    return [v for v in drop_repeats(variants, last_out) if not is_weak_reengagement(v)]


def reengagement_fallback(attempt=1, is_last=False):
    """Запасной вопрос, если модель снова ушла в «напишу потом»."""
    if is_last:
        return "Понял. Скинуть короткое КП, чтобы было под рукой?"
    return "Понял. А вакансии сейчас открыты или подбор пока не планируете?"


# --- Плейсхолдеры и незваное представление ---
# Живой оператор никогда не отправит «меня зовут [Имя]» и не будет сам по себе
# выдавать визитку с названием компании, если об этом не просили.
_PLACEHOLDER_PATTERNS = (
    re.compile(r"\[[^\]]{0,60}\]"),
    re.compile(r"\{[^}]{0,60}\}"),
    re.compile(r"<[^>]{0,60}>"),
    re.compile(r"_{2,}"),
    re.compile(r"\b[XxXxХх]{3,}\b"),
    re.compile(r"\(\s*(?:имя|ваше имя|имя лида|имя клиента|название|"
               r"название компании|компания|должность|город)\s*\)", re.IGNORECASE),
)

_SELF_INTRO_MARKERS = (
    "меня зовут", "мое имя", "я из", "мы из",
    "я представляю компанию", "мы представляем компанию",
    "hr group", "hrgroup", "хр груп", "эйчар груп", "эйч ар груп",
    "наша компания называется", "разрешите представиться",
    "позвольте представиться", "представлюсь",
)

_IDENTITY_QUESTIONS = (
    "кто вы", "вы кто", "кто это", "кто пишет", "что за человек",
    "как вас зовут", "как вас звать", "как к вам обращаться",
    "представьтесь", "представтесь", "представьте себя",
    "от кого", "откуда вы", "вы откуда", "вы от кого",
    "что за компания", "какая компания", "из какой компании",
    "название компании", "кого представляете", "что за фирма",
    "где работаете", "вы из какой",
)


def asks_identity(text):
    """Лид прямо спросил, кто мы и откуда — только тогда представляемся."""
    normalized = _normalize(text)
    if not normalized:
        return False
    return any(_normalize(m) in normalized for m in _IDENTITY_QUESTIONS)


def has_self_intro(text):
    """В тексте есть представление по имени или название компании."""
    normalized = _normalize(text)
    if not normalized:
        return False
    return any(_normalize(m) in normalized for m in _SELF_INTRO_MARKERS)


def has_placeholder(text):
    """В тексте осталась шаблонная переменная вроде [Имя] или {name}."""
    value = str(text or "")
    return any(pattern.search(value) for pattern in _PLACEHOLDER_PATTERNS)


def remove_placeholders(text):
    value = str(text or "")
    for pattern in _PLACEHOLDER_PATTERNS:
        value = pattern.sub(" ", value)
    return value


def _tidy(text):
    """Убрать следы вырезанных кусков: двойные пробелы, висячие запятые."""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"\(\s*\)", " ", value)
    value = re.sub(r"\s+([,.!?;:…])", r"\1", value)
    value = re.sub(r"([,;:])\s*(?=[,.;:!?…])", "", value)
    value = re.sub(r"\s{2,}", " ", value)
    value = value.strip(" ,;:—-").strip()
    return value[:1].upper() + value[1:] if value else ""


def clean_variant(text, allow_intro=False):
    """Убрать плейсхолдеры и незваное представление, сохранив суть."""
    value = _tidy(repair_broken_intro(remove_placeholders(text)))
    if not value:
        return ""
    if allow_intro:
        return value
    kept = []
    for part in re.split(r"(?<=[.!?…])\s+", value):
        part = part.strip()
        if not part:
            continue
        if not has_self_intro(part):
            kept.append(part)
            continue
        if "?" not in part:
            continue
        # В предложении сидит и визитка, и сам вопрос — режем только визитку.
        clauses = [c for c in part.split(",") if c.strip() and not has_self_intro(c)]
        rebuilt = _tidy(",".join(clauses))
        if rebuilt and "?" in rebuilt:
            kept.append(rebuilt)
    return _tidy(" ".join(kept))


def sanitize_variants(variants, allow_intro=False):
    """Очистить все варианты и выбросить пустые и повторы."""
    out, seen = [], set()
    for variant in (variants or []):
        cleaned = clean_variant(variant, allow_intro=allow_intro)
        if not cleaned:
            continue
        key = _normalize(cleaned)
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def neutral_fallback():
    """Крайний случай: модель дважды вернула только шаблоны и визитки."""
    return "Понял вас. Подскажите, вопрос найма сейчас актуален?"


# Если из фразы вырезали плейсхолдер с именем, остаётся обрубок «Меня зовут,».
_BROKEN_INTRO_PATTERNS = (
    re.compile(r"(?iu)\bмен[яе]\s+зовут\b\s*(?=[,.!?;:—-]|\s*$)"),
    re.compile(r"(?iu)\bмо[её]\s+имя\b\s*(?=[,.!?;:—-]|\s*$)"),
    re.compile(r"(?iu)\bя\s*[—-]\s*(?=[,.!?;:]|\s*$)"),
)


def repair_broken_intro(text):
    """Убрать «Меня зовут» без имени после удалённого плейсхолдера."""
    value = str(text or "")
    for pattern in _BROKEN_INTRO_PATTERNS:
        value = pattern.sub("", value)
    return value
