# Leadgen Operator

Автоматизация лидогенерации для HR/рекрутинга:
Telegram (Kurigram) + Gemini + веб-панель.

## Первый вход в Telegram
1. https://my.telegram.org -> API development tools -> создать приложение
2. Вписать API_ID и API_HASH в `.env` (PHONE уже заполнен)
3. Открыть `login.command` двойным кликом - откроется окно Terminal
4. Ввести код из Telegram в этом окне
5. Рядом появится `leadgen.session` - повторный вход не понадобится

## Структура
- `login.py`, `login.command` - одноразовый вход в Telegram
- `app/` - tg_client, dialog_engine, db, panel/
- `kb/` - база знаний в markdown (оффер, кейсы, возражения, скрипт куратора)
