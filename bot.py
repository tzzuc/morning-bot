import os
import json
import logging
from datetime import datetime, timedelta

import pytz
from anthropic import Anthropic
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import calendar_api

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = int(os.environ['CHAT_ID'])
TAIWAN_TZ = pytz.timezone('Asia/Taipei')
claude = Anthropic()


def parse_event_with_claude(text: str) -> dict:
    today = datetime.now(TAIWAN_TZ).strftime('%Y-%m-%d %A')
    response = claude.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=200,
        messages=[{
            'role': 'user',
            'content': (
                f'今天是 {today}。把以下文字解析成行事曆事件，只回傳 JSON，不要加任何說明或 markdown：\n\n'
                f'"{text}"\n\n'
                '格式：{"title":"...","date":"YYYY-MM-DD","start_time":"HH:MM","end_time":"HH:MM"}\n'
                '若無時間則用 09:00-10:00，若無日期則用今天。'
            ),
        }],
    )
    raw = response.content[0].text.strip()
    # 移除可能的 markdown code block
    if raw.startswith('```'):
        raw = raw.split('```')[1]
        if raw.startswith('json'):
            raw = raw[4:]
    return json.loads(raw.strip())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '嗨！我是你的行事曆助理 📅\n\n'
        '直接傳訊息新增事件，例如：\n'
        '「明天下午3點開會」\n'
        '「週五早上10點牙醫」\n\n'
        '/today - 今天的行程\n'
        '/tomorrow - 明天的行程\n'
        '/delete - 刪除事件'
    )


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TAIWAN_TZ)
    await send_day_events(update.effective_chat.id, now, context)


async def tomorrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tomorrow = datetime.now(TAIWAN_TZ) + timedelta(days=1)
    await send_day_events(update.effective_chat.id, tomorrow, context)


async def send_day_events(chat_id: int, date: datetime, context: ContextTypes.DEFAULT_TYPE):
    events = calendar_api.get_events(date)
    date_str = date.strftime('%m/%d (%a)')

    if not events:
        await context.bot.send_message(chat_id, f'📅 {date_str}\n\n✨ 沒有行程')
        return

    lines = [f'📅 {date_str}\n']
    for e in events:
        lines.append(f'• {calendar_api.format_event(e)}')
    await context.bot.send_message(chat_id, '\n'.join(lines))


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TAIWAN_TZ)
    events = calendar_api.get_events(now)

    if not events:
        await update.message.reply_text('今天沒有可刪除的行程 ✨')
        return

    keyboard = [
        [InlineKeyboardButton(calendar_api.format_event(e), callback_data=f"del_{e['id']}")]
        for e in events
    ]
    keyboard.append([InlineKeyboardButton('取消', callback_data='del_cancel')])
    await update.message.reply_text('選擇要刪除的行程：', reply_markup=InlineKeyboardMarkup(keyboard))


async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'del_cancel':
        await query.edit_message_text('已取消')
        return

    event_id = query.data.removeprefix('del_')
    calendar_api.delete_event(event_id)
    await query.edit_message_text('✅ 已刪除')


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    await update.message.reply_text('⏳ 解析中...')

    try:
        event = parse_event_with_claude(text)
        created = calendar_api.create_event(
            title=event['title'],
            date=event['date'],
            start_time=event['start_time'],
            end_time=event['end_time'],
        )
        await update.message.reply_text(
            f"✅ 已新增\n"
            f"📌 {event['title']}\n"
            f"📅 {event['date']} {event['start_time']}–{event['end_time']}"
        )
    except Exception as e:
        logging.error(e)
        await update.message.reply_text('❌ 解析失敗，請試試更明確的說法，例如「明天下午3點開會1小時」')


async def daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TAIWAN_TZ)
    await send_day_events(CHAT_ID, now, context)


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('today', today_command))
    app.add_handler(CommandHandler('tomorrow', tomorrow_command))
    app.add_handler(CommandHandler('delete', delete_command))
    app.add_handler(CallbackQueryHandler(delete_callback, pattern='^del_'))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    scheduler = AsyncIOScheduler(timezone=TAIWAN_TZ)
    # 每天早上 09:00 台灣時間
    scheduler.add_job(daily_reminder, 'cron', hour=9, minute=0, args=[app])
    scheduler.start()

    app.run_polling()


if __name__ == '__main__':
    main()
