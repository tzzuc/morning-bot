import os
from datetime import datetime, timedelta
import pytz
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TAIWAN_TZ = pytz.timezone('Asia/Taipei')
SCOPES = ['https://www.googleapis.com/auth/calendar']

MEETING_CAL_NAME = '🗓 會議'
WORK_CAL_NAME = '💼 工作規劃'

_calendar_ids: dict[str, str] = {}


def get_service():
    from google.auth.transport.requests import Request
    creds = Credentials(
        token=None,
        refresh_token=os.environ['GOOGLE_REFRESH_TOKEN'],
        client_id=os.environ['GOOGLE_CLIENT_ID'],
        client_secret=os.environ['GOOGLE_CLIENT_SECRET'],
        token_uri='https://oauth2.googleapis.com/token',
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build('calendar', 'v3', credentials=creds)


def get_calendar_id(calendar_type: str) -> str:
    if calendar_type in _calendar_ids:
        return _calendar_ids[calendar_type]

    name = MEETING_CAL_NAME if calendar_type == 'meeting' else WORK_CAL_NAME
    service = get_service()

    for cal in service.calendarList().list().execute().get('items', []):
        if cal['summary'] == name:
            _calendar_ids[calendar_type] = cal['id']
            return cal['id']

    new_cal = service.calendars().insert(body={
        'summary': name,
        'timeZone': 'Asia/Taipei',
    }).execute()
    _calendar_ids[calendar_type] = new_cal['id']
    return new_cal['id']


def get_events(date: datetime) -> list[dict]:
    service = get_service()
    start = TAIWAN_TZ.localize(date.replace(hour=0, minute=0, second=0, microsecond=0))
    end = start + timedelta(days=1)

    all_events = []
    seen = set()

    cal_ids = ['primary']
    try:
        cal_ids += [get_calendar_id('meeting'), get_calendar_id('work')]
    except Exception:
        pass

    for cal_id in cal_ids:
        items = service.events().list(
            calendarId=cal_id,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy='startTime',
        ).execute().get('items', [])
        for e in items:
            if e['id'] not in seen:
                seen.add(e['id'])
                all_events.append(e)

    all_events.sort(key=lambda e: e['start'].get('dateTime', e['start'].get('date', '')))
    return all_events


def create_event(title: str, date: str, start_time: str, end_time: str,
                 calendar_type: str = 'meeting', description: str = '') -> dict:
    service = get_service()
    cal_id = get_calendar_id(calendar_type)
    start_dt = TAIWAN_TZ.localize(datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M"))
    end_dt = TAIWAN_TZ.localize(datetime.strptime(f"{date} {end_time}", "%Y-%m-%d %H:%M"))
    event = {
        'summary': title,
        'description': description,
        'start': {'dateTime': start_dt.isoformat()},
        'end': {'dateTime': end_dt.isoformat()},
    }
    return service.events().insert(calendarId=cal_id, body=event).execute()


def update_event(event_id: str, **changes) -> dict:
    service = get_service()
    cal_ids = ['primary']
    try:
        cal_ids += [get_calendar_id('meeting'), get_calendar_id('work')]
    except Exception:
        pass

    for cal_id in cal_ids:
        try:
            event = service.events().get(calendarId=cal_id, eventId=event_id).execute()
        except Exception:
            continue

        if 'title' in changes:
            event['summary'] = changes['title']

        if any(k in changes for k in ('date', 'start_time', 'end_time')):
            current_start = datetime.fromisoformat(event['start']['dateTime']).astimezone(TAIWAN_TZ)
            current_end = datetime.fromisoformat(event['end']['dateTime']).astimezone(TAIWAN_TZ)
            date = changes.get('date', current_start.strftime('%Y-%m-%d'))
            start_time = changes.get('start_time', current_start.strftime('%H:%M'))
            end_time = changes.get('end_time', current_end.strftime('%H:%M'))
            start_dt = TAIWAN_TZ.localize(datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M"))
            end_dt = TAIWAN_TZ.localize(datetime.strptime(f"{date} {end_time}", "%Y-%m-%d %H:%M"))
            event['start'] = {'dateTime': start_dt.isoformat()}
            event['end'] = {'dateTime': end_dt.isoformat()}

        return service.events().update(calendarId=cal_id, eventId=event_id, body=event).execute()

    raise ValueError(f"Event {event_id} not found")


def delete_event(event_id: str) -> None:
    service = get_service()
    cal_ids = ['primary']
    try:
        cal_ids += [get_calendar_id('meeting'), get_calendar_id('work')]
    except Exception:
        pass
    for cal_id in cal_ids:
        try:
            service.events().delete(calendarId=cal_id, eventId=event_id).execute()
            return
        except Exception:
            continue


def get_free_slots(days_ahead: int = 7, duration_hours: float = 1.0) -> list[dict]:
    now = datetime.now(TAIWAN_TZ)
    slots = []

    for day_offset in range(1, days_ahead + 1):
        date = now + timedelta(days=day_offset)
        if date.weekday() >= 5:
            continue

        events = get_events(date)
        work_start = TAIWAN_TZ.localize(date.replace(hour=9, minute=0, second=0, microsecond=0))
        work_end = TAIWAN_TZ.localize(date.replace(hour=19, minute=0, second=0, microsecond=0))
        duration = timedelta(hours=duration_hours)

        busy = sorted([
            (datetime.fromisoformat(e['start']['dateTime']).astimezone(TAIWAN_TZ),
             datetime.fromisoformat(e['end']['dateTime']).astimezone(TAIWAN_TZ))
            for e in events if e['start'].get('dateTime')
        ])

        cursor = work_start
        for b_start, b_end in busy:
            if cursor + duration <= b_start:
                slots.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'start': cursor.strftime('%H:%M'),
                    'end': (cursor + duration).strftime('%H:%M'),
                    'display': f"{date.strftime('%m/%d (%a)')} {cursor.strftime('%H:%M')}–{(cursor + duration).strftime('%H:%M')}",
                })
                if len(slots) >= 3:
                    return slots
            cursor = max(cursor, b_end)

        if cursor + duration <= work_end:
            slots.append({
                'date': date.strftime('%Y-%m-%d'),
                'start': cursor.strftime('%H:%M'),
                'end': (cursor + duration).strftime('%H:%M'),
                'display': f"{date.strftime('%m/%d (%a)')} {cursor.strftime('%H:%M')}–{(cursor + duration).strftime('%H:%M')}",
            })

        if len(slots) >= 3:
            break

    return slots[:3]


def search_events(keyword: str, days_ahead: int = 30) -> list[dict]:
    now = datetime.now(TAIWAN_TZ)
    keyword_lower = keyword.lower()
    matches = []
    seen = set()
    for day_offset in range(days_ahead):
        for e in get_events(now + timedelta(days=day_offset)):
            if e['id'] not in seen and keyword_lower in e.get('summary', '').lower():
                seen.add(e['id'])
                matches.append(e)
    return matches


def format_event(event: dict) -> str:
    start = event['start'].get('dateTime', event['start'].get('date', ''))
    time_str = datetime.fromisoformat(start).astimezone(TAIWAN_TZ).strftime('%H:%M') if 'T' in start else '全天'
    return f"{time_str} {event['summary']}"
