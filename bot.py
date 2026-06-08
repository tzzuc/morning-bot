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


def _strip_json(text: str) -> str:
    text = text.strip()
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
    return text.strip()


def analyze_message(text: str) -> dict:
    today = datetime.now(TAIWAN_TZ).strftime('%Y-%m-%d %A')
    response = claude.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=400,
        messages=[{'role': 'user', 'content': (
            f'今天是 {today}。分析訊息，只回傳 JSON，不加任何說明或 markdown。\n\n'
            f'訊息："{text}"\n\n'
            '判斷 intent：\n'
            '- "add_event"：新增一個全新的事件\n'
            '- "suggest_slots"：想找可行時段安排會議（有「約」「找時間」「安排」等詞且沒有明確時間）\n'
            '- "edit_event"：修改、更改、調整、改成、換成、移到 已存在的事件（有「改」「更改」「調整」「移到」「換成」等詞）\n'
            '- "other"：其他\n\n'
            '判斷 calendar_type（新增時用）：\n'
            '- "meeting"：與他人的會議、約定、電話\n'
            '- "work"：個人工作任務、專注時間\n'
            '- "kahowa"：有提到 Kahowa 的事件\n\n'
            '回傳格式（所有欄位都要有，沒有的填 null）：\n'
            '{"intent":"...","calendar_type":"meeting|work","event":{"title":"...","date":"YYYY-MM-DD","start_time":"HH:MM","end_time":"HH:MM"},"duration_hours":1,"search_query":"...","changes":{"title":"...","date":"YYYY-MM-DD","start_time":"HH:MM","end_time":"HH:MM","calendar_type":"meeting|work"}}\n\n'
            '若 intent 為 edit_event：\n'
            '- search_query：用來搜尋事件的關鍵字（事件名稱關鍵字）\n'
            '- changes：只含要修改的欄位；若要換日曆類型（如工作規劃改成會議），加入 calendar_type 欄位\n'
            '若 intent 為 suggest_slots 或 add_event，changes 和 search_query 為 null。\n'
            '若無明確日期用今天，若無時間用09:00-10:00。'
        )}],
    )
    return json.loads(_strip_json(response.content[0].text))


def parse_edit(text: str, event: dict) -> dict:
    today = datetime.now(TAIWAN_TZ).strftime('%Y-%m-%d %A')
    start = event['start'].get('dateTime', '')
    end = event['end'].get('dateTime', '')
    response = claude.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=200,
        messages=[{'role': 'user', 'content': (
            f'今天是 {today}。\n'
            f'原事件：標題「{event.get("summary", "")}」，開始 {start}，結束 {end}\n'
            f'使用者說："{text}"\n\n'
            '回傳要修改的欄位（只含要改的），不加說明或 markdown：\n'
            '{"title":"新標題","date":"YYYY-MM-DD","start_time":"HH:MM","end_time":"HH:MM","calendar_type":"meeting|work"}\n'
            '若要改成會議用 calendar_type: "meeting"，改成工作規劃用 calendar_type: "work"。'
        )}],
    )
    return json.loads(_strip_json(response.content[0].text))


async def calendars_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        cals = calendar_api.list_calendars()
        lines = ['你的所有日曆（複製 ID 到 Railway 環境變數）：\n']
        for c in cals:
            lines.append(f"📅 {c['name']}\n`{c['id']}`\n")
        await update.message.reply_text('\n'.join(lines), parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f'❌ {e}')


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '嗨！我是你的行事曆助理 📅\n\n'
        '直接傳訊息：\n'
        '「今天下午2點 Vesta 報告 2小時」\n'
        '「幫我約 John 下週開會一小時」→ 我幫你找空檔\n\n'
        '/today — 今天的行程\n'
        '/tomorrow — 明天的行程\n'
        '/edit — 修改事件\n'
        '/delete — 刪除事件'
    )


async def send_day_events(chat_id: int, date: datetime, context: ContextTypes.DEFAULT_TYPE):
    date_str = date.strftime('%m/%d (%a)')
    try:
        events = calendar_api.get_events(date)
    except Exception as e:
        logging.error(e)
        await context.bot.send_message(chat_id, f'❌ 無法讀取行事曆\n\n錯誤：{e}')
        return
    if not events:
        await context.bot.send_message(chat_id, f'📅 {date_str}\n\n✨ 沒有行程')
        return
    lines = [f'📅 {date_str}\n'] + [f'• {calendar_api.format_event(e)}' for e in events]
    await context.bot.send_message(chat_id, '\n'.join(lines))


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_day_events(update.effective_chat.id, datetime.now(TAIWAN_TZ), context)


async def tomorrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_day_events(update.effective_chat.id, datetime.now(TAIWAN_TZ) + timedelta(days=1), context)


async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TAIWAN_TZ)
    events = []
    try:
        for i in range(7):
            events.extend(calendar_api.get_events(now + timedelta(days=i)))
    except Exception as e:
        logging.error(e)
        await update.message.reply_text('❌ 無法讀取行事曆，請稍後再試')
        return

    if not events:
        await update.message.reply_text('未來7天沒有可修改的行程 ✨')
        return

    events = events[:10]
    context.user_data['edit_events'] = {str(i): e for i, e in enumerate(events)}

    keyboard = [
        [InlineKeyboardButton(calendar_api.format_event(e), callback_data=f"edit_{i}")]
        for i, e in enumerate(events)
    ]
    keyboard.append([InlineKeyboardButton('取消', callback_data='edit_cancel')])
    await update.message.reply_text('選擇要修改的行程：', reply_markup=InlineKeyboardMarkup(keyboard))


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        events = calendar_api.get_events(datetime.now(TAIWAN_TZ))
    except Exception as e:
        logging.error(e)
        await update.message.reply_text('❌ 無法讀取行事曆，請稍後再試')
        return
    if not events:
        await update.message.reply_text('今天沒有可刪除的行程 ✨')
        return

    context.user_data['delete_events'] = {str(i): e for i, e in enumerate(events)}
    keyboard = [
        [InlineKeyboardButton(calendar_api.format_event(e), callback_data=f"del_{i}")]
        for i, e in enumerate(events)
    ]
    keyboard.append([InlineKeyboardButton('取消', callback_data='del_cancel')])
    await update.message.reply_text('選擇要刪除的行程：', reply_markup=InlineKeyboardMarkup(keyboard))


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == 'confirm_edit_yes':
        event = context.user_data.pop('confirm_edit_event', None)
        changes = context.user_data.pop('pending_edit_changes', {})
        if event and changes:
            try:
                calendar_api.update_event(event['id'], **changes)
                await query.edit_message_text(f"✅ 已修改：{event.get('summary', '')}")
            except Exception as e:
                logging.error(e)
                await query.edit_message_text('❌ 修改失敗，請再試一次')
        elif event:
            context.user_data['editing_event'] = event
            await query.edit_message_text(
                f"好，修改「{event.get('summary', '')}」\n\n請告訴我要改什麼，例如：\n「改到週五下午3點」\n「標題改成 Thryve 會議」"
            )

    elif data == 'confirm_edit_no':
        context.user_data.pop('confirm_edit_event', None)
        context.user_data.pop('pending_edit_changes', None)
        await query.edit_message_text('好，請重新描述你想修改的事件')

    elif data.startswith('confirm_edit_'):
        idx = data.removeprefix('confirm_edit_')
        event = context.user_data.pop('confirm_edit_matches', {}).get(idx)
        changes = context.user_data.pop('pending_edit_changes', {})
        if event and changes:
            try:
                calendar_api.update_event(event['id'], **changes)
                await query.edit_message_text(f"✅ 已修改：{event.get('summary', '')}")
            except Exception as e:
                logging.error(e)
                await query.edit_message_text('❌ 修改失敗，請再試一次')
        elif event:
            context.user_data['editing_event'] = event
            await query.edit_message_text(
                f"好，修改「{event.get('summary', '')}」\n\n請告訴我要改什麼："
            )

    elif data == 'edit_cancel':
        context.user_data.pop('editing_event', None)
        context.user_data.pop('edit_events', None)
        await query.edit_message_text('已取消')

    elif data.startswith('edit_'):
        idx = data.removeprefix('edit_')
        event = context.user_data.get('edit_events', {}).get(idx)
        if event:
            context.user_data['editing_event'] = event
            await query.edit_message_text(
                f"正在修改：{calendar_api.format_event(event)}\n\n"
                f"請告訴我要改什麼，例如：\n"
                f"「改到明天下午3點」\n「標題改成 Thryve 會議」\n「改成2小時」"
            )

    elif data == 'del_cancel':
        context.user_data.pop('delete_events', None)
        await query.edit_message_text('已取消')

    elif data.startswith('del_'):
        idx = data.removeprefix('del_')
        event = context.user_data.get('delete_events', {}).get(idx)
        if event:
            calendar_api.delete_event(event['id'])
            await query.edit_message_text(f"✅ 已刪除：{event.get('summary', '')}")

    elif data == 'slot_cancel':
        context.user_data.pop('pending_slots', None)
        context.user_data.pop('pending_title', None)
        context.user_data.pop('pending_cal_type', None)
        await query.edit_message_text('已取消')

    elif data.startswith('slot_'):
        idx = int(data.removeprefix('slot_'))
        slots = context.user_data.pop('pending_slots', [])
        title = context.user_data.pop('pending_title', '會議')
        cal_type = context.user_data.pop('pending_cal_type', 'meeting')
        if idx < len(slots):
            s = slots[idx]
            calendar_api.create_event(title=title, date=s['date'], start_time=s['start'],
                                      end_time=s['end'], calendar_type=cal_type)
            cal_label = calendar_api.CAL_LABELS.get(cal_type, cal_type)
            await query.edit_message_text(
                f"✅ 已新增到「{cal_label}」\n📌 {title}\n📅 {s['date']} {s['start']}–{s['end']}"
            )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    # Edit mode
    if 'editing_event' in context.user_data:
        event = context.user_data.pop('editing_event')
        await update.message.reply_text('⏳ 修改中...')
        try:
            changes = parse_edit(text, event)
            calendar_api.update_event(event['id'], **changes)
            await update.message.reply_text(f"✅ 已修改：{event.get('summary', '')}")
        except Exception as e:
            logging.error(e)
            await update.message.reply_text('❌ 修改失敗，請再試一次')
        return

    await update.message.reply_text('⏳ 解析中...')
    try:
        result = analyze_message(text)

        if result['intent'] == 'suggest_slots':
            duration = result.get('duration_hours', 1)
            slots = calendar_api.get_free_slots(days_ahead=7, duration_hours=duration)
            if not slots:
                await update.message.reply_text('未來7天工作時段已排滿 😅')
                return

            title = (result.get('event') or {}).get('title', '會議')
            context.user_data['pending_slots'] = slots
            context.user_data['pending_title'] = title
            context.user_data['pending_cal_type'] = result.get('calendar_type', 'meeting')

            keyboard = [
                [InlineKeyboardButton(s['display'], callback_data=f"slot_{i}")]
                for i, s in enumerate(slots)
            ]
            keyboard.append([InlineKeyboardButton('取消', callback_data='slot_cancel')])
            await update.message.reply_text('以下是可行的時段，選一個：',
                                            reply_markup=InlineKeyboardMarkup(keyboard))

        elif result['intent'] == 'add_event' and result.get('event'):
            e = result['event']
            cal_type = result.get('calendar_type', 'meeting')
            calendar_api.create_event(title=e['title'], date=e['date'],
                                      start_time=e['start_time'], end_time=e['end_time'],
                                      calendar_type=cal_type)
            cal_label = calendar_api.CAL_LABELS.get(cal_type, cal_type)
            await update.message.reply_text(
                f"✅ 已新增到「{cal_label}」\n📌 {e['title']}\n📅 {e['date']} {e['start_time']}–{e['end_time']}"
            )

        elif result['intent'] == 'edit_event':
            query = result.get('search_query', '')
            matches = calendar_api.search_events(query) if query else []
            if not matches:
                await update.message.reply_text(f'找不到包含「{query}」的事件 🔍')
                return

            changes = result.get('changes') or {}
            context.user_data['pending_edit_changes'] = changes

            if len(matches) == 1:
                event = matches[0]
                context.user_data['confirm_edit_event'] = event
                keyboard = [
                    [InlineKeyboardButton('✅ 對，改這個', callback_data='confirm_edit_yes'),
                     InlineKeyboardButton('❌ 不是', callback_data='confirm_edit_no')]
                ]
                detail = calendar_api.format_event(event)
                await update.message.reply_text(
                    f'找到這個事件：\n📌 {detail}\n\n是這個嗎？',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                context.user_data['confirm_edit_matches'] = {str(i): e for i, e in enumerate(matches[:5])}
                keyboard = [
                    [InlineKeyboardButton(calendar_api.format_event(e), callback_data=f"confirm_edit_{i}")]
                    for i, e in enumerate(matches[:5])
                ]
                keyboard.append([InlineKeyboardButton('取消', callback_data='edit_cancel')])
                await update.message.reply_text(
                    f'找到多個包含「{query}」的事件，選一個：',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

        else:
            await update.message.reply_text(
                '沒有看懂，請試試：\n'
                '「今天下午2點 Vesta 報告 2小時」\n'
                '「幫我約 John 下週開會一小時」\n'
                '「Vesta 下週會議改到週五下午3點」'
            )

    except Exception as e:
        logging.error(e)
        await update.message.reply_text(f'❌ 解析失敗\n\n{e}')


async def daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    await send_day_events(CHAT_ID, datetime.now(TAIWAN_TZ), context)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('calendars', calendars_command))
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('today', today_command))
    app.add_handler(CommandHandler('tomorrow', tomorrow_command))
    app.add_handler(CommandHandler('edit', edit_command))
    app.add_handler(CommandHandler('delete', delete_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    scheduler = AsyncIOScheduler(timezone=TAIWAN_TZ)
    scheduler.add_job(daily_reminder, 'cron', hour=9, minute=0, args=[app])
    scheduler.start()

    app.run_polling()


if __name__ == '__main__':
    main()
