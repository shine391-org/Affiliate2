import json,unittest,hashlib,math,os,sys,secrets
from cryptography.fernet import Fernet
from pathlib import Path
from dataclasses import asdict
ROOT=Path(os.environ['TIMING_ARTIFACTS_ROOT']).resolve()
os.environ.update(VIRALMINT_DATA_DIR=str(ROOT/'tests-isolation'),
                 SECRET_KEY=secrets.token_urlsafe(48),
                 ENCRYPTION_KEY=Fernet.generate_key().decode())
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'app'))
from backend.services.dubbing_planner import DubbingConfig,PlanItem,DubbingChunk,allocate_silence
CORPUS=json.loads((ROOT/'chunk-corpus.json').read_text(encoding='utf-8'))
class CorpusEvidenceTests(unittest.TestCase):
 def test_all_eight_independent_chinese_sources_no_english_or_rerun_counting(self):
  sources=CORPUS['dataset']['sources']
  self.assertEqual(len(sources),8)
  self.assertEqual(len({r['identity_key'] for r in sources}),8)
  self.assertTrue(all(r['language']=='zh' for r in sources))
  self.assertEqual(len([r for r in sources if r['source_id'].startswith('legacy:')]),2)
 def test_every_unit_and_audio_is_counted(self):
  sources=CORPUS['dataset']['sources']
  self.assertEqual(sum(r['segments'] for r in sources),sum(len(r['segment_ids']) for r in CORPUS['chunks']))
  for source in sources:
   rows=[r for r in CORPUS['chunks'] if r['source_id']==source['source_id']]
   ids=[i for r in rows for i in r['segment_ids']]
   self.assertEqual(len(ids),len(set(ids)))
   self.assertEqual(len(ids),source['segments'])
   for row in rows:
    audio=Path(row['audio_path'])
    self.assertTrue(audio.is_file())
    self.assertEqual(hashlib.sha256(audio.read_bytes()).hexdigest(),row['audio_sha256'])
    self.assertGreater(row['recorded_trimmed_ms'],0)
    self.assertIsNone(row['raw_before_trim_ms'])
 def test_original_five_and_chunk43_grounded_in_exact_manifest(self):
  reg=CORPUS['dataset']['regression']
  self.assertEqual(reg['chunk_ids'],[39,43,54,55,56])
  manifest=json.loads(Path(reg['manifest_path']).read_text(encoding='utf-8'))
  summary=manifest['stages']['dubbing']['error']
  for cid in reg['chunk_ids']:
   self.assertIn(f'chunk {cid}:',summary)
   self.assertTrue(any(r['source_id']==reg['source_id'] and r['chunk_id']==cid for r in CORPUS['chunks']))
  row=next(r for r in CORPUS['chunks'] if r['source_id']==reg['source_id'] and r['chunk_id']==43)
  self.assertEqual(row['spoken_text'],'Được, lại đây, hướng về')
  self.assertEqual(row['voice'],'en-US-AndrewMultilingualNeural')
 def test_allocator_replay_exact_and_idempotent(self):
  for video in CORPUS['videos']:
   config=DubbingConfig(**video['config']);plan=[PlanItem(**x) for x in video['plan']];chunks=[DubbingChunk(**x) for x in video['chunks']]
   expected=[(x.id,x.start_ms,x.end_ms) for x in chunks]
   for repeat in range(2):
    allocate_silence(plan,chunks,config,timeline_end_ms=video['duration_ms'])
    self.assertEqual(expected,[(x.id,x.start_ms,x.end_ms) for x in chunks])
   for row,chunk in zip([r for r in CORPUS['chunks'] if r['source_id']==video['source_id']],chunks):
    self.assertAlmostEqual(row['required_speed_allocated'],max(1,chunk.actual_duration_ms/chunk.available_ms))
    self.assertEqual(row['allocated_window_ms'],chunk.available_ms)
 def test_no_omission_or_voice_switch_in_saved_input(self):
  for row in CORPUS['chunks']:
   self.assertTrue(row['spoken_text'].strip())
   self.assertTrue(row['voice'])
   self.assertEqual(len({x['speaker_id'] for x in row['items']}),1)
   self.assertEqual(len({x['voice_id'] for x in row['items']}),1)
   self.assertTrue(all(x['translation_status']!='omitted' for x in row['source_segments']))
if __name__=='__main__':unittest.main(verbosity=2)