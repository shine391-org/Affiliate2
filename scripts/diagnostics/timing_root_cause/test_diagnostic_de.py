import os
import copy,hashlib,json,subprocess,unittest
from pathlib import Path
from de_experiment import boundary_reason
ROOT=Path(os.environ['TIMING_ARTIFACTS_ROOT']).resolve()
CORPUS=json.loads((ROOT/'chunk-corpus.json').read_text(encoding='utf-8'))
RESULT=json.loads((ROOT/'de/results.json').read_text(encoding='utf-8'))
class DiagnosticDETests(unittest.TestCase):
 def test_full_eight_video_boundary_and_overflow_accounting(self):
  self.assertEqual(len(RESULT['videos']),8)
  self.assertEqual(len(RESULT['boundary_decisions']),sum(x['chunk_count']-1 for x in CORPUS['videos']))
  expected={(r['source_id'],r['chunk_id']) for r in CORPUS['chunks'] if r['required_speed_original']>next(v['config']['speed_max'] for v in CORPUS['videos'] if v['source_id']==r['source_id'])}
  self.assertEqual({(r['source_id'],r['chunk_id']) for r in RESULT['e_overflows']},expected)
  self.assertEqual(len(RESULT['e_overflows']),len(expected))
 def test_live_audio_is_real_hash_matched_and_probe_matched(self):
  pair=RESULT['pairs'][0]
  self.assertEqual(pair['status'],'LIVE_PAIRED')
  for sample in [*pair['a'],pair['b']]:
   raw=Path(sample['raw_path']);trim=Path(sample['trimmed_path'])
   self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(),sample['raw_sha256'])
   for path,expected in [(raw,sample['raw_ms']),(trim,sample['trimmed_ms'])]:
    result=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(path)],capture_output=True,text=True,check=True)
    d=json.loads(result.stdout);self.assertIn('audio',[s['codec_type'] for s in d['streams']]);self.assertEqual(round(float(d['format']['duration'])*1000),expected)
 def test_only_raw_pair_not_one_video_rescued(self):
  self.assertEqual(len(RESULT['pairs']),1)
  pair=RESULT['pairs'][0]
  self.assertEqual(pair['canonical_chunk_ids'],[59,60])
  self.assertEqual(' '.join(x['text'] for x in pair['a']).split(),pair['b']['text'].split())
  self.assertEqual(sum(x['trimmed_ms'] for x in pair['a']),pair['a_total_trimmed_ms'])
  self.assertEqual(pair['b']['trimmed_ms'],pair['b_trimmed_ms'])
  video=next(v for v in RESULT['videos'] if v['source_id']==pair['source_id'])
  self.assertGreater(video['a']['hard_overflow_groups'],0)
  self.assertGreater(video['b']['hard_overflow_groups'],0)
  self.assertEqual(video['a']['groups']-video['b']['groups'],1)
  self.assertIsNone(RESULT['auto_complete_video_rate'])
 def test_speaker_voice_strong_boundary_lock_and_span_are_hard(self):
  pair=RESULT['pairs'][0];rows=[r for r in CORPUS['chunks'] if r['source_id']==pair['source_id'] and r['chunk_id'] in [59,60]]
  left,right=rows
  self.assertFalse(boundary_reason(left,right,[left],set())[0])
  mutations=[('speaker','different','speaker_turn'),('voice','different','voice_boundary')]
  for key,value,expected in mutations:
   x=copy.deepcopy(right);x[key]=value;self.assertIn(expected,boundary_reason(left,x,[left],set())[0])
  x=copy.deepcopy(left);x['items'][-1]['spoken_text']+='.'
  self.assertIn('strong_sentence_boundary',boundary_reason(x,right,[x],set())[0])
  x=copy.deepcopy(right);x['items'][0]['timing_locked']=True
  self.assertIn('timing_lock',boundary_reason(left,x,[left],set())[0])
  self.assertIn('canonical_forced_split',boundary_reason(left,right,[left],{right['segment_ids'][0]})[0])
  x=copy.deepcopy(right);x['original_end_ms']=left['original_start_ms']+8001
  self.assertIn('utterance_span_over_8000',boundary_reason(left,x,[left],set())[0])
 def test_e_keeps_raw_unknown_and_exact_speed_arithmetic(self):
  for row in RESULT['e_overflows']:
   self.assertIsNone(row['raw_before_trim_ms'])
   self.assertIsNone(row['original_required_speed_raw'])
   self.assertAlmostEqual(row['required_speed_after_current_allocator'],max(1,row['trimmed_ms']/row['allocated_window_ms']))
   self.assertEqual(row['allocation_gained_ms'],row['allocated_window_ms']-row['original_window_ms'])
   self.assertLessEqual(row['reserve_before_ms'],350)
   self.assertLessEqual(row['reserve_after_ms'],350)
  probes=[json.loads(s) for s in (ROOT/'de/silence-probes.jsonl').read_text(encoding='utf-8').splitlines() if s]
  self.assertEqual(len({p['source'] for p in probes}),8)
  self.assertTrue(all(p['exit']==0 for p in probes))
if __name__=='__main__':unittest.main(verbosity=2)