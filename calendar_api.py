import os
from datetime import datetime, timedelta
import pytz
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TAIWAN_TZ = pytz.timezone('Asia/Taipei')
SCOPES = ['https://www.googleapis.com/auth/calendar']


def get_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ['GOOGLE_REFRESH_TOKEN'],
        client_id=os.environ['GOOGLE_CLIENT_ID'],
        client_secret=os.environ['GOOGLE_CLIENT_SECRET'],
        token_uri='https://oauth2.googleapis.com/token',
        scopes=SCOPES,
    )
    return build('calendar', 'v3', credentials=creds)


def get_events(date: datetime) -> list[dict]:
    service = get_service()
    start = TAIWAN_TZ.localize(date.replace(hour=0, minute=0, second=0, microsecond=0))
    end = start + timedelta(days=1)

    result = service.events().list(
        calendarId='primary',
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy='startTime',
    ).execute()

    return result.get('items', [])


def create_event(title: str, date: str, start_time: str, end_time: str, description: str = '') -> dict:
    service = get_service()

    start_dt = TAIWAN_TZ.localize(datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M"))
    end_dt = TAIWAN_TZ.localize(datetime.strptime(f"{date} {end_time}", "%Y-%m-%d %H:%M"))

    event = {
        'summary': title,
        'description': description,
        'start': {'dateTime': start_dt.isoformat()},
        'end': {'dateTime': end_dt.isoformat()},
    }

    return service.events().insert(calendarId='primary', body=event).execute()


def delete_event(event_id: str) -> None:
    service = get_service()
    service.events().delete(calendarId='primary', eventId=event_id).execute()


def format_event(event: dict) -> str:
    start = event['start'].get('dateTime', event['start'].get('date', ''))
    if 'T' in start:
        dt = datetime.fromisoformat(start).astimezone(TAIWAN_TZ)
        time_str = dt.strftime('%H:%M')
    else:
        time_str = '全天'
    return f"{time_str} {event['summary']}"
