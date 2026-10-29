import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dispatcher.models import TaskDefinition
from dispatcher.modules.broadcast import (
    _universal_cut_output_name,
    run_universal_cut,
)
from dispatcher.modules.common import TaskContext


def make_context(task, root):
    return TaskContext(
        task=task, settings={'timezone': 'Europe/Moscow'}, secrets={},
        run_dir=root/'run', cancel_event=threading.Event(),
        log=lambda _text: None, progress=lambda _n,_text: None,
    )


class OutputFilenameTests(unittest.TestCase):
    def test_exact_example_uses_two_digit_year(self):
        name = _universal_cut_output_name(
            'Новости столица {date} {hour-minute}',
            Path('source_12-08-2026_18-00.mp3'),
            'Столица',
        )
        self.assertEqual(name, 'Новости столица 12-08-26 18-00.mp3')

    def test_long_date_seconds_and_extension(self):
        name = _universal_cut_output_name(
            '{source} {date-full} {hour}-{minute}-{second}.MP3',
            Path('source_12-08-2026_18-03-12.mp3'), 'Республика',
        )
        self.assertEqual(name, 'Республика 12-08-2026 18-03-12.mp3')

    def test_unknown_variable_explained(self):
        with self.assertRaisesRegex(ValueError, 'Неизвестная переменная'):
            _universal_cut_output_name('{impossible}', Path('tmp_12-08-2026_18-00.mp3'), 'S')

    def test_rename_in_cash_and_upload_same_name_to_both_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'source'; source.mkdir()
            for hour in ('18', '19'):
                (source/f'12-08-2026_{hour}-00.mp3').write_bytes(b'input')
                (source/f'12-08-2026_{hour}-00.cue').write_text('cue')
            task = TaskDefinition(id='t', name='t', module='broadcast.universal_cut', params={
                'date_mode': 'manual', 'manual_date': '12-08-2026',
                'cut_mode': 'between', 'start_marker': 'start', 'end_marker': 'end',
                'sources': [{'name': 'Столица', 'folder': str(source),
                             'filename': 'Новости столица {date} {hour-minute}'}],
                'output_dir': str(root/'cash'),
                'internal_bot_enabled': True, 'internal_bot_republic_enabled': True,
                'cleanup_after_send': False,
            })
            sent = []
            class FakeBot:
                def __init__(self, ctx, channel='', title=''):
                    self.channel=channel
                def send_group(self, title, paths, **kwargs):
                    sent.append((self.channel,[p.name for p in paths],kwargs.get('filename_template')))
            def fake_export(ctx, mp3, cue, output, prefix, start, end):
                output_file = output/f'{prefix}_{cue.stem}.mp3'
                output_file.write_bytes(b'processed')
                return output_file
            with patch('dispatcher.modules.broadcast.InternalBotClient', FakeBot), \
                 patch('dispatcher.modules.broadcast.export_between_markers', side_effect=fake_export):
                result=run_universal_cut(make_context(task, root))
            names=[Path(p).name for p in result['files']]
            self.assertEqual(names, ['Новости столица 12-08-26 18-00.mp3',
                                     'Новости столица 12-08-26 19-00.mp3'])
            self.assertEqual(sorted(p.name for p in (root/'cash').iterdir()), sorted(names))
            self.assertEqual(sent, [
                ('extracted_media', names, '{original}'),
                ('archive_respublika', names, '{original}'),
            ])

    def test_collisions_do_not_overwrite_another_generated_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            src=root/'source';src.mkdir()
            for hr in ['18','19']:
                (src/f'12-08-2026_{hr}-00.mp3').write_bytes(b'mp3')
                (src/f'12-08-2026_{hr}-00.cue').write_bytes(b'cue')
            task=TaskDefinition(id='a',name='a',module='broadcast.universal_cut', params={
                'date_mode':'manual','manual_date':'12-08-2026',
                'cut_mode':'between','start_marker':'in','end_marker':'out',
                'sources':[{'name':'A','folder':str(src),'filename':'Новости {date}'}],
                'output_dir':str(root/'cash'), 'internal_bot_enabled':False,
            })
            def exporter(ctx,mp3,cue,output,prefix,start,end):
                path=output/f'{prefix}_{cue.stem}.mp3'
                path.write_bytes(cue.stem.encode())
                return path
            with patch('dispatcher.modules.broadcast.export_between_markers',side_effect=exporter):
                result=run_universal_cut(make_context(task,root))
            self.assertEqual({Path(x).name for x in result['files']},
                             {'Новости 12-08-26.mp3','Новости 12-08-26_2.mp3'})
            self.assertEqual(len({Path(x).read_bytes() for x in result['files']}),2)

if __name__ == '__main__': unittest.main()
