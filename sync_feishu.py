"""Read authorized Feishu Minutes and file course material locally."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import course_assistant as ca

ROOT = ca.ROOT


def cli_path():
    exe = Path(os.environ['CLASSDOCK_LARK_CLI']) if os.environ.get('CLASSDOCK_LARK_CLI') else Path(os.environ.get('APPDATA', '')) / 'npm/node_modules/@larksuite/cli/bin/lark-cli.exe'
    if not exe.is_file():
        raise RuntimeError('未找到飞书官方连接工具，请先安装并完成用户授权')
    return exe


def call_cli(args):
    env = dict(os.environ, LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1', LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1')
    proc = subprocess.run([str(cli_path()), *args, '--as', 'user', '--format', 'json'], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding='utf-8', timeout=90)
    try:
        data = json.loads(proc.stdout or proc.stderr)
    except json.JSONDecodeError:
        raise RuntimeError('飞书返回格式异常；没有归档，不打印可能含账号信息的完整响应')
    if proc.returncode or not data.get('ok'):
        error = data.get('error', {})
        raise RuntimeError(f'飞书读取失败：{error.get("subtype", "unknown")}；{error.get("message", "unknown")}')
    return data


def fetch_day(day, cfg):
    items, token = [], None
    while True:
        args = ['minutes', '+search', '--owner-ids', 'me', '--start', day+'T00:00:00+08:00', '--end', day+'T23:59:59+08:00']
        if token:
            args += ['--page-token', token]
        result = call_cli(args)['data']
        items.extend(result.get('items', []))
        if not result.get('has_more'):
            break
        token = result.get('page_token')
        if not token:
            raise RuntimeError('飞书提示还有结果但缺少分页标识')
    seen = set()
    records = []
    for item in items:
        minute = item.get('token')
        if not minute or minute in seen:
            continue
        seen.add(minute)
        metadata = item.get('meta_data', {})
        title = item.get('title') or metadata.get('title') or item.get('display_info', '').split('\n')[0]
        confirmation = cfg.get('confirmed_minutes', {}).get(minute, {})
        cid = confirmation.get('course')
        if cid not in {c['id'] for c in cfg['courses']}:
            cid, _ = ca.identify(cfg, title, '')
        if cid and not next(c for c in cfg['courses'] if c['id'] == cid).get('archive_enabled', True):
            continue
        snapshot = call_cli(['minutes', '+detail', '--minute-tokens', minute, '--summary', '--transcript', '--output-dir', './local/飞书原始资料/'+day])
        raw_path = ROOT / 'local/飞书原始资料' / day / (minute+'.json')
        ca.save_json(raw_path, snapshot)
        for record in snapshot['data'].get('minutes', []):
            record['source_url'] = item.get('meta_data', {}).get('app_link', '')
            record['search_description'] = item.get('meta_data', {}).get('description', '')
            records.append(record)
    return records


def archive_records(cfg, day, records):
    staging = ROOT / 'local/待归档飞书资料' / day
    staging.mkdir(parents=True, exist_ok=True)
    links = {}
    deferred = []
    for record in records:
        title = record.get('title', '')
        artifacts = record.get('artifacts', {})
        summary = artifacts.get('summary', '')
        transcript_path = Path(artifacts.get('transcript_file', ''))
        if not transcript_path.is_absolute():
            transcript_path = ROOT / transcript_path
        transcript = transcript_path.read_text(encoding='utf-8-sig') if transcript_path.is_file() else ''
        match = re.match(r'(20\d{2}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})', transcript)
        if not match:
            match = re.search(r'开始时间:\s*(20\d{2})\.(\d{2})\.(\d{2})\s+(\d{2}:\d{2}:\d{2})', record.get('search_description', ''))
            actual_day = '-'.join(match.groups()[:3]) if match else None
            actual_time = match.group(4) if match else None
        else:
            actual_day, actual_time = match.groups()
        if actual_day != day:
            deferred.append({'title': title, 'status': '待确认', 'reason': '录音日期缺失或与请求日期不同'})
            continue
        confirmation = cfg.get('confirmed_minutes', {}).get(record.get('minute_token'), {})
        if confirmation:
            cid, reason = confirmation.get('course'), confirmation.get('match_method', '用户确认课程')
            if cid not in {c['id'] for c in cfg['courses']}:
                deferred.append({'title': title, 'status': '待确认', 'reason': '确认课程映射无效，请核对配置'})
                continue
        else:
            cid, reason = ca.identify(cfg, title, summary or transcript)
        if not cid:
            deferred.append({'title': title, 'status': '待确认', 'reason': reason})
            continue
        if not summary and not transcript:
            deferred.append({'title': title, 'status': '待确认', 'reason': 'AI 总结与逐字稿均为空'})
            continue
        course = next(c for c in cfg['courses'] if c['id'] == cid)
        if not course.get('archive_enabled', True):
            deferred.append({'title': title, 'status': '未启用归档，跳过'})
            continue
        url = record.get('source_url', '')
        if not url:
            deferred.append({'title': title, 'status': '待确认', 'reason': '缺少可核对的来源链接'})
            continue
        kind = '飞书AI总结' if summary else '飞书逐字稿（暂无AI总结）'
        body = '\n'.join([f'# {title}', f'资料类型：{kind}', f'录音开始：{actual_day} {actual_time}（北京时间）', f'来源链接：{url}',
                          '保留飞书原文，未额外改写；AI 总结及语音转写可能有误，请结合逐字稿与老师课件核对。', '', summary or transcript])
        filename = f'{day}_{ca.safe_name(course["name"])}_{kind}_{record["minute_token"]}.md'
        src = staging / filename
        if not src.exists() or src.read_text(encoding='utf-8') != body:
            src.write_text(body, encoding='utf-8')
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        links[filename] = {'feishu_url': url, 'recording_start': actual_day+' '+actual_time, 'minute_token': record['minute_token'],
                           'document_type': kind, 'course_match': reason, '_key': f'{day}:{cid}:{digest}', '_transcript_path': str(transcript_path) if transcript else None}
    # Only ingest files represented by this verified snapshot, excluding stale staging files.
    run_inbox = staging / ('batch-' + hashlib.sha256(json.dumps(sorted(links)).encode()).hexdigest()[:16])
    run_inbox.mkdir(parents=True, exist_ok=True)
    for filename in links:
        shutil.copy2(staging / filename, run_inbox / filename)
    report = ca.ingest(cfg, run_inbox, day) if links else []
    index_path = ROOT / '归档记录/index.json'
    if index_path.is_file():
        index = ca.read_json(index_path)
        by_key = {info['_key']: info for info in links.values()}
        for row in index:
            info = by_key.get(row['key'])
            if info:
                row.update({k: v for k, v in info.items() if not k.startswith('_')})
                if info['_transcript_path'] and not row.get('transcript'):
                    source = Path(info['_transcript_path'])
                    if row.get('lesson_folder'):
                        backup = Path(row['lesson_folder']) / '课堂逐字稿.txt'
                    else:
                        transcript_stem = Path(row['pdf']).stem.replace('飞书AI总结', '逐字稿').replace('飞书逐字稿', '逐字稿')
                        backup = Path(row['original']).with_name(transcript_stem+'.txt')
                    if backup.exists() and backup.read_bytes() != source.read_bytes():
                        raise RuntimeError('逐字稿目标已存在且内容不同，请核对')
                    if not backup.exists():
                        shutil.copy2(source, backup)
                    row['transcript'] = str(backup)
        ca.save_json(index_path, index)
    return {'date': day, 'records_found': len(records), 'archive': report, 'deferred': deferred}


def main():
    parser = argparse.ArgumentParser(description='同步飞书课程资料到桌面，仅执行云端读取')
    parser.add_argument('--date', default=ca.today())
    parser.add_argument('--snapshot', help='使用已保存的真实查询结果，无需重复请求飞书')
    args = parser.parse_args()
    from datetime import date
    date.fromisoformat(args.date)
    cfg = ca.read_json(ca.CONFIG)
    if args.snapshot:
        records = ca.read_json(args.snapshot)['data']['minutes']
    else:
        records = fetch_day(args.date, cfg)
    result = archive_records(cfg, args.date, records)
    ca.save_json(ROOT / 'local' / ('飞书同步报告-'+args.date+'.json'), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
