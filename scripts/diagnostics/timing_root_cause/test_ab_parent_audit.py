import os
import copy,json,unittest
from pathlib import Path
import ab_parent_audit as a
ROOT=Path(os.environ['TIMING_ARTIFACTS_ROOT']).resolve()
class ParentABTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.c=json.loads((ROOT/'chunk-corpus.json').read_text(encoding='utf-8'))
  cls.report=json.loads((ROOT/'ab/parent-strategy-audit.json').read_text(encoding='utf-8'))
  cls.base=a.unique(a.rows('baseline.jsonl'));cls.rates=a.unique(a.rows('experiment_a.jsonl'),True)
 def test_full_unique_baseline_coverage(self):self.assertEqual(set(self.base),{a.key(x) for x in self.c['chunks']});self.assertEqual(len(self.base),154)
 def test_resume_cold_and_reload_statistical_identity(self):
  data=a.rows('baseline.jsonl');first=a.unique(data[:70]);first.update(a.unique(data[70:]));resumed=a.unique(json.loads(json.dumps(data)))
  self.assertEqual(first,resumed)
  durations=lambda rows:{k:r.get('trimmed_duration_ms') or r['raw_duration_ms'] for k,r in rows.items()}
  self.assertEqual(a.evaluate(self.c,durations(first)),a.evaluate(self.c,durations(resumed)))
 def test_duplicate_and_error_records_fail_closed(self):
  r=a.rows('baseline.jsonl')[0]
  with self.assertRaises(AssertionError):a.unique([r,r])
  with self.assertRaises(AssertionError):a.unique([{**r,'error':'timeout'}])
 def test_all_hard_and_five_regressions_have_three_rate_arms(self):
  hard={(v['source_id'],i) for v in self.report['strategies']['baseline']['videos'] for i in v['hard_ids']}
  hard|={(self.c['dataset']['regression']['source_id'],i) for i in [39,43,54,55,56]}
  for k in hard:
   for rate in [1.1,1.2,1.3]:self.assertIn((*k,rate),self.rates)
 def test_two_fitting_controls_per_video(self):
  for v in self.report['strategies']['baseline']['videos']:
   excluded=set(v['hard_ids'])|set(v['soft_ids'])
   controls={k[1] for k in self.rates if k[0]==v['source_id'] and k[1] not in excluded}
   self.assertGreaterEqual(len(controls),2,v['source_id'])
 def test_exact_retime_reproduces_all_eleven_historical_issue_values(self):
  durations={a.key(r):r['recorded_trimmed_ms'] for r in self.c['chunks']}
  replay=a.evaluate(self.c,durations);reg=self.c['dataset']['regression'];v=next(v for v in replay['videos'] if v['source_id']==reg['source_id']);chunks={c['chunk_id']:c for c in v['chunks']}
  manifest=json.loads(Path(reg['manifest_path']).read_text(encoding='utf-8'));issues=[r for r in manifest['stages']['dubbing']['metadata']['issues'] if r.get('reason')=='timing_overflow']
  self.assertEqual(len(issues),11);self.assertEqual({i['chunk_id'] for i in issues},set(v['hard_ids']))
  for i in issues:
   c=chunks[i['chunk_id']];self.assertEqual(round(c['required_speed'],4),i['required_speed']);self.assertEqual(c['output_window_ms'],i['available_ms']);self.assertEqual(c['duration_ms'],i['tts_duration_ms'])
 def test_required_speed_is_canonical_clamped_not_duration_ratio(self):
  for s in self.report['strategies'].values():
   self.assertIsNone(s['auto_complete_video_rate'])
   for v in s['videos']:
    for c in v['chunks']:self.assertEqual(c['required_speed'],max(1,c['duration_ms']/c['output_window_ms']))
 def test_word_preservation_not_semantic_pause_acceptance(self):
  self.assertIn('NOT VERIFIED',self.report['strategies']['B_punctuation']['prosody_semantic_acceptance'])
  r=a.rows('experiment_b.jsonl');self.assertEqual(len(r),25);self.assertEqual(sum(bool(x.get('trimmed_duration_ms')) for x in r),14)
 def test_live_anomaly_repeat_complete_and_original_evidence_preserved(self):
  repeats=json.loads((ROOT/'ab/parent-rate-repeats/report.json').read_text(encoding='utf-8'))
  self.assertEqual(len(repeats['rows']),8);self.assertTrue(all(x['status']=='LIVE' for x in repeats['rows']));self.assertEqual(len(repeats['paired']),4)
  retrim=json.loads((ROOT/'ab/parent-rate-repeats/original-retrim.json').read_text(encoding='utf-8'));self.assertEqual([x['repeat_canonical_trim_of_original_raw_ms'] for x in retrim],[938,939])
if __name__=='__main__':unittest.main(verbosity=2)