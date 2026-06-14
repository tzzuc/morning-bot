import os
from datetime import datetime
import pytz
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TAIWAN_TZ = pytz.timezone('Asia/Taipei')
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/tasks',
]


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
    return build('tasks', 'v1', credentials=creds)


def _default_list_id() -> str:
    service = get_service()
    lists = service.tasklists().list().execute().get('items', [])
    return lists[0]['id'] if lists else '@default'


def add_task(title: str, due_date: str | None = None, notes: str = '') -> dict:
    service = get_service()
    body: dict = {'title': title, 'notes': notes}
    if due_date:
        # Google Tasks 只看 due 的日期部分，時間會被忽略
        body['due'] = f"{due_date}T00:00:00.000Z"
    return service.tasks().insert(tasklist=_default_list_id(), body=body).execute()


def list_tasks(include_completed: bool = False) -> list[dict]:
    service = get_service()
    params = {'tasklist': _default_list_id(), 'showCompleted': include_completed}
    if not include_completed:
        params['showHidden'] = False
    items = service.tasks().list(**params).execute().get('items', [])
    # 有 due 的依日期排序在前，沒 due 的在後
    return sorted(items, key=lambda t: (not t.get('due'), t.get('due', '')))


def complete_task(task_id: str) -> dict:
    service = get_service()
    return service.tasks().patch(
        tasklist=_default_list_id(),
        task=task_id,
        body={'status': 'completed'},
    ).execute()


def delete_task(task_id: str) -> None:
    service = get_service()
    service.tasks().delete(tasklist=_default_list_id(), task=task_id).execute()


def format_task(task: dict) -> str:
    title = task.get('title', '')
    due = task.get('due', '')
    if due:
        due_dt = datetime.fromisoformat(due.replace('Z', '+00:00'))
        due_str = due_dt.strftime('%m/%d')
        return f"{due_str} {title}"
    return title
