"""Independent audit of persisted real A/B audio and canonical timing replay."""
import copy,hashlib,json,math,os,re,secrets,subprocess,sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from cryptography.fernet import Fernet
ROOT=Path(os.environ['TIMING_ARTIFACTS_ROOT']).resolve()
os.environ.update(VIRALMINT_DATA_DIR=str(ROOT/'ab-parent-isolation'),SECRET_KEY=secrets.token_urlsafe(48),ENCRYPTION_KEY=Fernet.generate_key().decode())
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'app'))
from backend.services.dubbing_planner import DubbingConfig,DubbingChunk,PlanItem,allocate_silence,required_speed
from backend.services.localization_service import _scale_chunks_for_video_slowdown

def rows(name):
 return [json.loads(s) for s in (ROOT/'ab/results'/name).read_text(encoding='utf-8').splitlines() if s.strip()]
def key(r):return (r['source_id'],r['chunk_id'])
def unique(records,with_rate=False):
 out={}
 for r in records:
  k=(*key(r),r['rate']) if with_rate else key(r)
  if k in out:raise AssertionError(('duplicate measurement',k))
  assert not r.get('error'),r
  out[k]=r
 return out

def percentile(xs,p):
 s=sorted(xs);i=(len(s)-1)*p;j=int(i);return round(s[j]+(s[min(j+1,len(s)-1)]-s[j])*(i-j),4)
def distribution(xs):return {k:percentile(xs,p) for k,p in [('p50',.5),('p90',.9),('p95',.95)]}
def evaluate(corpus,durations):
 all_speeds=[];videos=[]
 for v in corpus['videos']:
  plan=[PlanItem(**x) for x in v['plan']];chunks=[DubbingChunk(**x) for x in v['chunks']];config=DubbingConfig(**v['config']);p=v['profile']
  for c in chunks:c.actual_duration_ms=durations[(v['source_id'],c.id)]
  allocate_silence(plan,chunks,config,timeline_end_ms=v['duration_ms'])
  allocated={c.id:{'start_ms':c.start_ms,'end_ms':c.end_ms,'window_ms':c.available_ms,'required_speed':required_speed(c.actual_duration_ms,c.available_ms)} for c in chunks}
  speeds=[required_speed(c.actual_duration_ms,c.available_ms) for c in chunks]
  needed=max([1.0]+[s/config.speed_max for s in speeds if s>config.speed_max]);factor=1.0
  if needed>1 and p['video_retime_mode']=='fallback':
   factor=min(needed,p['video_retime_max_slowdown'])
   if any(required_speed(c.actual_duration_ms,round(c.end_ms*factor)-round(c.start_ms*factor))>config.speed_max for c in chunks):
    safe=max((math.ceil(c.actual_duration_ms/config.speed_max)+1)/c.available_ms for c in chunks)
    factor=min(max(factor,safe),p['video_retime_max_slowdown'])
   _scale_chunks_for_video_slowdown(chunks,factor)
  measured=[];hard=[];soft=[]
  for c in chunks:
   speed=required_speed(c.actual_duration_ms,c.available_ms);all_speeds.append(speed)
   status='hard' if speed>config.emergency_speed_max+1e-6 else 'soft' if speed>config.speed_max+1e-6 else 'fit'
   if status=='hard':hard.append(c.id)
   if status=='soft':soft.append(c.id)
   measured.append({'chunk_id':c.id,'duration_ms':c.actual_duration_ms,'allocated':allocated[c.id],'output_window_ms':c.available_ms,'required_speed':speed,'status':status})
  videos.append({'source_id':v['source_id'],'total_chunks':len(chunks),'slowdown':factor,'hard_ids':hard,'soft_ids':soft,'timing_feasible':not hard,'chunks':measured})
 return {'scope':'LOCAL canonical allocator+retime projection on LIVE leaf audio; not render/QC','auto_complete_video_rate':None,'timing_feasible_video_count':sum(x['timing_feasible'] for x in videos),'hard_overflow_chunks':sum(len(x['hard_ids']) for x in videos),'soft_overflow_chunks':sum(len(x['soft_ids']) for x in videos),'total_chunks':len(all_speeds),'speed_distribution':distribution(all_speeds),'videos':videos}

def audit():
 corpus=json.loads((ROOT/'chunk-corpus.json').read_text(encoding='utf-8'));cmap={key(r):r for r in corpus['chunks']}
 baseline=unique(rows('baseline.jsonl'));a=unique(rows('experiment_a.jsonl'),True);b=unique(rows('experiment_b.jsonl'))
 assert set(baseline)==set(cmap) and len(baseline)==154
 assets=[]
 for k,r in baseline.items():
  assert r['spoken_text']==cmap[k]['spoken_text'] and r['voice']==cmap[k]['voice']
  assets.append({'arm':'baseline','key':list(k),'path':r['raw_path'],'expected_ms':r['raw_duration_ms'],'expected_sha':r['raw_sha256']})
  if r.get('trimmed_path'):assets.append({'arm':'baseline_trim','key':list(k),'path':r['trimmed_path'],'expected_ms':r['trimmed_duration_ms'],'expected_sha':r['trimmed_sha256']})
 for k,r in a.items():
  assert r['spoken_text']==cmap[k[:2]]['spoken_text'] and r['voice']==cmap[k[:2]]['voice']
  stem=f"{r['source_id'].replace(':','_').replace('/','_')}_chunk{r['chunk_id']:03d}_r{int(r['rate']*100)}"
  for suffix,field in [('_raw.mp3','raw_duration_ms'),('_trimmed.wav','trimmed_duration_ms')]:
   if r.get(field):assets.append({'arm':'A','key':list(k),'path':str(ROOT/'ab/audio'/(stem+suffix)),'expected_ms':r[field],'expected_sha':None})
 for k,r in b.items():
  assert r['original_text']==cmap[k]['spoken_text'] and r['voice']==cmap[k]['voice']
  assert re.findall(r'\w+',r['original_text'])==re.findall(r'\w+',r['normalized_text']),r
  if r.get('trimmed_duration_ms'):
   stem=f"{r['source_id'].replace(':','_').replace('/','_')}_chunk{r['chunk_id']:03d}_norm"
   for suffix,field in [('_raw.mp3','raw_duration_ms'),('_trimmed.wav','trimmed_duration_ms')]:
    assets.append({'arm':'B','key':list(k),'path':str(ROOT/'ab/normalized'/(stem+suffix)),'expected_ms':r[field],'expected_sha':None})
 def probe(row):
  path=Path(row['path']);sha=hashlib.sha256(path.read_bytes()).hexdigest()
  if row['expected_sha']:assert sha==row['expected_sha'],row
  proc=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(path)],capture_output=True,text=True,check=True)
  d=json.loads(proc.stdout);ms=round(float(d['format']['duration'])*1000)
  assert any(x.get('codec_type')=='audio' for x in d['streams']),row
  assert abs(ms-row['expected_ms'])<=1,(row,ms)
  return {**row,'actual_ms':ms,'sha256':sha}
 with ThreadPoolExecutor(max_workers=6) as pool:verified=list(pool.map(probe,assets))
 (ROOT/'ab/parent-audio-audit.json').write_text(json.dumps(verified,ensure_ascii=False,indent=2),encoding='utf-8')
 base_d={k:r.get('trimmed_duration_ms') or r['raw_duration_ms'] for k,r in baseline.items()}
 strategies={'baseline':evaluate(corpus,base_d)}
 for rate in [1.1,1.2,1.3]:
  ds=base_d.copy();tested=[]
  for k,r in a.items():
   if r['rate']==rate:ds[k[:2]]=r.get('trimmed_duration_ms') or r['raw_duration_ms'];tested.append(k[:2])
  strategies[f'A_rate_{rate}']=evaluate(corpus,ds);strategies[f'A_rate_{rate}']['measured_changed_chunks']=len(tested)
 ds=base_d.copy()
 for k,r in b.items():
  if r.get('trimmed_duration_ms'):ds[k]=r['trimmed_duration_ms']
 strategies['B_punctuation']=evaluate(corpus,ds);strategies['B_punctuation']['prosody_semantic_acceptance']='NOT VERIFIED; lexical preservation only. Candidates may remove meaningful pauses.'
 baseline_hard={(v['source_id'],i) for v in strategies['baseline']['videos'] for i in v['hard_ids']}
 for name,result in strategies.items():
  h={(v['source_id'],i) for v in result['videos'] for i in v['hard_ids']}
  result['rescued_baseline_hard_chunks']=[list(k) for k in sorted(baseline_hard-h)]
  result['new_hard_chunks']=[list(k) for k in sorted(h-baseline_hard)]
  result['newly_timing_feasible_videos']=[v['source_id'] for v,base in zip(result['videos'],strategies['baseline']['videos']) if v['timing_feasible'] and not base['timing_feasible']]
 controls={v['source_id']:len({k[:2] for k in a if k[0]==v['source_id']} - {k for k in baseline_hard if k[0]==v['source_id']}) for v in corpus['videos']}
 report={'scope':'independent parent audit; exact text/voice and actual files verified','validated_audio_files':len(verified),'baseline_rows':len(baseline),'rate_rows':len(a),'punctuation_rows':len(b),'B_synthesized':sum(bool(r.get('trimmed_duration_ms')) for r in b.values()),'selected_non_hard_test_chunks_by_video':controls,'strategies':strategies,'naturalness':'NOT VERIFIED','semantic_guard_failure_rate':'NOT APPLICABLE to unchanged words; B semantic pauses unverified','production_changes':False}
 (ROOT/'ab/parent-strategy-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'validated_audio_files':len(verified),'strategies':{k:{'hard':v['hard_overflow_chunks'],'soft':v['soft_overflow_chunks'],'timing_feasible_videos':v['timing_feasible_video_count'],'newly_feasible':v['newly_timing_feasible_videos'],'rescued_hard_chunks':v['rescued_baseline_hard_chunks'],'new_hard_chunks':v['new_hard_chunks'],'speeds':v['speed_distribution']} for k,v in strategies.items()}},ensure_ascii=False,indent=2))
 return report
if __name__=='__main__':audit()