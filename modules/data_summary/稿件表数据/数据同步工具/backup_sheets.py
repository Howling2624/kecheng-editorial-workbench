# backup_sheets.py
# Google 表格 / Excel 直接覆盖式备份
# 输出目录：脚本同目录下的「稿件表备份」

import os
import io
import json
import re
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import pickle

SCOPES = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/spreadsheets.readonly'
]

# ==============================
# 路径与配置
# ==============================

# 脚本所在目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR) if os.path.basename(SCRIPT_DIR) == '数据同步工具' else SCRIPT_DIR

# 固定备份目录：稿件表数据/稿件表备份
BACKUP_DIR = os.path.join(BASE_DIR, '稿件表备份')

# 优先读取脚本同目录的 config.json；兼容旧项目结构里的 dashboard/config.json
config_candidates = [
    os.path.join(SCRIPT_DIR, 'config.json'),
    os.path.join(BASE_DIR, 'config.json'),
    os.path.join(os.path.dirname(BASE_DIR), 'dashboard', 'config.json'),
]
config_path = next((path for path in config_candidates if os.path.exists(path)), None)
if not config_path:
    tried = '\n'.join(config_candidates)
    raise FileNotFoundError(f'找不到配置文件，已尝试:\n{tried}')

with open(config_path, 'r', encoding='utf-8') as f:
    config = json.load(f)

# Google Drive 文件夹 ID
FOLDER_ID = config['google_drive_folder_id']

# ==============================
# 认证
# ==============================

def authenticate():
    creds = None
    token_path = os.path.join(SCRIPT_DIR, 'token.pickle')

    if os.path.exists(token_path):
        with open(token_path, 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            credentials_path = os.path.join(SCRIPT_DIR, 'credentials.json')
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(f'找不到认证文件: {credentials_path}')

            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_path, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(token_path, 'wb') as token:
            pickle.dump(creds, token)

    return creds

# ==============================
# Drive 文件读取
# ==============================

def get_files_in_folder(service, folder_id):
    """获取文件夹内所有 Google 表格 / Excel（含快捷方式）"""
    query = f"'{folder_id}' in parents and trashed=false"
    results = service.files().list(
        q=query,
        fields="files(id, name, mimeType, shortcutDetails)",
        pageSize=100
    ).execute()

    files = results.get('files', [])
    target_files = []

    for f in files:
        mime = f.get('mimeType')

        if mime == 'application/vnd.google-apps.spreadsheet':
            target_files.append({
                'id': f['id'],
                'name': f['name'],
                'type': 'google_sheets'
            })

        elif mime == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
            target_files.append({
                'id': f['id'],
                'name': f['name'],
                'type': 'excel'
            })

        elif mime == 'application/vnd.google-apps.shortcut':
            details = f.get('shortcutDetails', {})
            t_id = details.get('targetId')
            t_mime = details.get('targetMimeType')

            if t_mime == 'application/vnd.google-apps.spreadsheet':
                target_files.append({
                    'id': t_id,
                    'name': f['name'],
                    'type': 'google_sheets'
                })
            elif t_mime == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
                target_files.append({
                    'id': t_id,
                    'name': f['name'],
                    'type': 'excel'
                })

    return target_files

# ==============================
# 工具函数
# ==============================

def clean_filename(filename):
    illegal = '<>:"/\\|?*'
    for ch in illegal:
        filename = filename.replace(ch, '_')
    return filename.strip('. ')

SPECIAL_BACKUP_NAMES = config.get('special_backup_names', {})

def get_backup_filename(file_name):
    """统一生成备份文件名，例如 JOURNAL-A总表 -> JOURNAL-A稿件表.xlsx"""
    base_name = os.path.splitext(file_name)[0].strip()

    if base_name in SPECIAL_BACKUP_NAMES:
        return clean_filename(SPECIAL_BACKUP_NAMES[base_name] + '.xlsx')

    match = re.match(r'([A-Za-z0-9]+)', base_name)
    if match:
        code = match.group(1).upper()
        return clean_filename(f'{code}稿件表.xlsx')

    return clean_filename(base_name + '.xlsx')

def export_sheet(service, file_id, file_name, output_path):
    """导出 Google Sheet 为 Excel"""
    request = service.files().export_media(
        fileId=file_id,
        mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

    safe_name = get_backup_filename(file_name)
    file_path = os.path.join(output_path, safe_name)

    fh = io.FileIO(file_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    print(f'[OK] 覆盖导出：{safe_name}')

def download_excel(service, file_id, file_name, output_path):
    """下载原始 Excel"""
    request = service.files().get_media(fileId=file_id)

    safe_name = get_backup_filename(file_name)

    file_path = os.path.join(output_path, safe_name)

    fh = io.FileIO(file_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    print(f'[OK] 覆盖下载：{safe_name}')

# ==============================
# 主流程
# ==============================

def main():
    print('===== Google 表格 / Excel 直接覆盖备份 =====')
    print(f'备份目录：{os.path.abspath(BACKUP_DIR)}')

    os.makedirs(BACKUP_DIR, exist_ok=True)

    creds = authenticate()
    service = build('drive', 'v3', credentials=creds)

    files = get_files_in_folder(service, FOLDER_ID)
    if not files:
        print('未找到任何文件。')
        return

    print(f'待备份文件数：{len(files)}')

    for f in files:
        if f['type'] == 'google_sheets':
            export_sheet(service, f['id'], f['name'], BACKUP_DIR)
        else:
            download_excel(service, f['id'], f['name'], BACKUP_DIR)

    print('===== 全部覆盖完成 =====')

if __name__ == '__main__':
    main()
