"""Fictional fixtures only: routing, archive integrity and cloud read boundaries."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import course_assistant as ca
import sync_feishu as sync


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(ca, 'ROOT', self.root)
        self.sync_patch = patch.object(sync, 'ROOT', self.root)
        self.root_patch.start()
        self.sync_patch.start()
        self.cfg = {'desktop': str(self.root / 'desktop'), 'archive_layout': 'flat', 'courses': [
            {'id': 'a', 'name': '示例课程', 'folder': '示例课程', 'aliases': ['示例课程'], 'archive_enabled': True},
            {'id': 'b', 'name': '示例课程实验', 'folder': '实验', 'aliases': ['示例课程实验'], 'archive_enabled': False}],
            'lessons': [{'course': 'a', 'weekday': 5, 'weeks': [1]}, {'course': 'b', 'weekday': 5, 'weeks': [1]}]}
        self.inbox = self.root / 'inbox'
        self.inbox.mkdir()
        self.day = '2026-01-02'

    def tearDown(self):
        self.sync_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    def export(self, name, body):
        path = self.inbox / name
        path.write_text(body, encoding='utf-8')
        return path

    def fake_pdf(self, path, *args):
        # PDF layout is verified separately; these tests focus on filing rules.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture PDF')

    def test_disabled_course_is_not_filed_or_in_month_bundle(self):
        self.export(self.day+'_示例课程实验.txt', '示例课程实验课堂资料')
        report = ca.ingest(self.cfg, self.inbox, self.day)
        self.assertEqual(report[0]['status'], '未启用归档，跳过')
        self.assertFalse((self.root / 'desktop').exists())
        self.assertEqual([r['course'] for r in ca.month_bundle(self.cfg, '2026-01')], ['示例课程'])
        self.assertEqual([r['course'] for r in ca.candidates(self.cfg, self.day)['lessons']], ['a'])

    def test_chronological_names_collision_and_dedup(self):
        self.export(self.day+'_示例课程_record-a.md', '资料类型：飞书AI总结\n录音开始：2026-01-02 09:00:00\n示例课程 第一段')
        self.export(self.day+'_示例课程_record-b.md', '资料类型：飞书AI总结\n录音开始：2026-01-02 08:00:00\n示例课程 第二段')
        self.export(self.day+'_示例课程_record-c.md', '资料类型：飞书AI总结\n录音开始：2026-01-02 09:00:00\n示例课程 修订段')
        with patch.object(ca, 'write_pdf', side_effect=self.fake_pdf):
            result = ca.ingest(self.cfg, self.inbox, self.day)
        self.assertEqual([r['status'] for r in result], ['已归档']*3)
        names = sorted(Path(r['pdf']).name for r in result)
        self.assertEqual(names, [self.day+'_08-00-00_示例课程_飞书AI总结.pdf', self.day+'_09-00-00_示例课程_飞书AI总结.pdf', self.day+'_09-00-00_示例课程_飞书AI总结_02.pdf'])
        with patch.object(ca, 'write_pdf') as writer:
            repeated = ca.ingest(self.cfg, self.inbox, self.day)
        writer.assert_not_called()
        self.assertTrue(all(r['status'] == '已归档，跳过重复' for r in repeated))
        index = ca.read_json(self.root / '归档记录/index.json')
        self.assertEqual(len(index), 3)
        self.assertEqual(len({r['sha256'] for r in index}), 3)
        self.assertEqual(len({Path(r['original']).read_bytes() for r in index}), 3)
        ca.month_bundle(self.cfg, '2026-01')
        bundle = (self.root / '月度材料/2026-01/示例课程_材料与复习任务.md').read_text(encoding='utf-8')
        self.assertLess(bundle.index('第二段'), bundle.index('第一段'))

    def test_missing_date_and_ambiguous_name_do_not_guess(self):
        self.export('课堂记录.txt', '没有明确的课程名称')
        self.assertEqual(ca.ingest(self.cfg, self.inbox, None)[0]['status'], '失败')
        self.assertEqual(ca.ingest(self.cfg, self.inbox, self.day)[0]['status'], '待确认')

    def test_course_folder_cannot_escape_desktop(self):
        course = dict(self.cfg['courses'][0], folder='../outside')
        with self.assertRaises(ValueError):
            ca.course_folder(self.cfg, course)

    def test_disabled_search_result_never_requests_transcript(self):
        result = {'data': {'items': [{'token': 'fictional-token', 'display_info': '示例课程实验\n说明'}], 'has_more': False}}
        with patch.object(sync, 'call_cli', return_value=result) as cli:
            self.assertEqual(sync.fetch_day(self.day, self.cfg), [])
        self.assertEqual(cli.call_count, 1)

    def test_transcript_links_survive_repeated_sync(self):
        transcript = self.root / 'transcript.txt'
        transcript.write_text(self.day+' 09:00:00 CST\n示例课程 00:00:01 示例发言', encoding='utf-8')
        record = {'title': '示例课程', 'minute_token': 'fictional-token', 'source_url': 'https://example.invalid/minutes/fictional',
                  'artifacts': {'summary': '仅为虚构测试内容。', 'transcript_file': str(transcript)}}
        with patch.object(ca, 'write_pdf', side_effect=self.fake_pdf):
            first = sync.archive_records(self.cfg, self.day, [record])
            second = sync.archive_records(self.cfg, self.day, [record])
        self.assertEqual(first['archive'][0]['status'], '已归档')
        self.assertEqual(second['archive'][0]['status'], '已归档，跳过重复')
        index = ca.read_json(self.root / '归档记录/index.json')
        self.assertEqual(len(index), 1)
        self.assertNotIn('fictional-token', Path(index[0]['transcript']).name)
        self.assertEqual(Path(index[0]['transcript']).read_bytes(), transcript.read_bytes())
        self.cfg['courses'][0]['archive_enabled'] = False
        with patch.object(ca, 'write_pdf') as writer:
            disabled = sync.archive_records(self.cfg, self.day, [record])
        writer.assert_not_called()
        self.assertEqual(disabled['archive'], [])

    def test_lesson_folder_keeps_summary_transcript_and_original_together(self):
        self.cfg['archive_layout'] = 'lesson_folders'
        transcript = self.root / 'transcript.txt'
        transcript.write_text(self.day+' 09:00:00 CST\n仅为虚构课堂转写。', encoding='utf-8')
        record = {'title': '示例课程', 'minute_token': 'fictional-token', 'source_url': 'https://example.invalid/minutes/fictional',
                  'artifacts': {'summary': '第一版虚构课堂总结。', 'transcript_file': str(transcript)}}
        with patch.object(ca, 'write_pdf', side_effect=self.fake_pdf):
            sync.archive_records(self.cfg, self.day, [record])
            repeated = sync.archive_records(self.cfg, self.day, [record])
            record['artifacts']['summary'] = '第二版虚构课堂总结。'
            sync.archive_records(self.cfg, self.day, [record])
        self.assertEqual(repeated['archive'][0]['status'], '已归档，跳过重复')
        index = ca.read_json(self.root / '归档记录/index.json')
        self.assertEqual(len(index), 2)
        folders = [Path(row['lesson_folder']) for row in index]
        self.assertEqual([p.name for p in folders], [self.day+'_09-00-00_示例课程', self.day+'_09-00-00_示例课程_02'])
        for row, folder in zip(index, folders):
            self.assertEqual(Path(row['pdf']), folder / '课堂总结.pdf')
            self.assertEqual(Path(row['transcript']), folder / '课堂逐字稿.txt')
            self.assertEqual(Path(row['original']).parent, folder / '原始资料')
            self.assertEqual(Path(row['text']).parent, folder / '原始资料')
            self.assertEqual(Path(row['transcript']).read_bytes(), transcript.read_bytes())
        ca.month_bundle(self.cfg, '2026-01')
        bundle = (self.root / '月度材料/2026-01/示例课程_材料与复习任务.md').read_text(encoding='utf-8')
        self.assertIn('第一版虚构课堂总结', bundle)
        self.assertIn('第二版虚构课堂总结', bundle)

    def test_course_confirmation_applies_to_one_record_only(self):
        self.cfg['archive_layout'] = 'lesson_folders'
        self.cfg['confirmed_minutes'] = {'confirmed-fictional-token': {'course': 'a'}}
        transcript = self.root / 'transcript.txt'
        transcript.write_text(self.day+' 09:00:00 CST\n只有虚构教学主题，没有课程名。', encoding='utf-8')
        record = {'title': '虚构教学主题', 'minute_token': 'confirmed-fictional-token', 'source_url': 'https://example.invalid/confirmed',
                  'artifacts': {'summary': '虚构总结没有课程名。', 'transcript_file': str(transcript)}}
        with patch.object(ca, 'write_pdf', side_effect=self.fake_pdf):
            accepted = sync.archive_records(self.cfg, self.day, [record])
            repeated = sync.archive_records(self.cfg, self.day, [record])
            record['minute_token'] = 'unconfirmed-fictional-token'
            unconfirmed = sync.archive_records(self.cfg, self.day, [record])
        self.assertEqual(accepted['archive'][0]['status'], '已归档')
        self.assertEqual(repeated['archive'][0]['status'], '已归档，跳过重复')
        self.assertEqual(unconfirmed['archive'], [])
        self.assertEqual(unconfirmed['deferred'][0]['status'], '待确认')
        index = ca.read_json(self.root / '归档记录/index.json')
        self.assertEqual(len(index), 1)
        self.assertEqual(index[0]['course_match'], '用户确认课程')


if __name__ == '__main__':
    unittest.main()
