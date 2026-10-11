"""Isolated measured D grouping and canonical E allocator diagnostics."""
import asyncio,copy,hashlib,json,os,re,secrets,subprocess,sys,time
from pathlib import Path
from dataclasses import asdict
from cryptography.fernet import Fernet
ROOT=Path(os.environ['TIMING_ARTIFACTS_ROOT']).resolve()
WORK=ROOT/'de';WORK.mkdir(exist_ok=True)
os.environ.update(VIRALMINT_DATA_DIR=str(WORK/'isolated-data'),SECRET_KEY=secrets.token_urlsafe(48),ENCRYPTION_KEY=Fernet.generate_key().decode())
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'app'))
from backend.services.dubbing_planner import PlanItem,DubbingChunk,DubbingConfig,allocate_silence,_STRONG_END
from backend.services.edge_tts_service import generate_voice
from backend.services.localization_service import _trim_edge_tts_boundary_silence_sync
from backend.services.video_utils import probe_duration
CORPUS=json.loads((ROOT/'chunk-corpus.json').read_text(encoding='utf-8'))
def save(name,data):
 (WORK/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def append(name,row):
 with (WORK/name).open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
def original_bounds(row):return row['original_start_ms'],row['original_end_ms']
def boundary_reason(previous,current,group,forced):
 reasons=[];p=previous['source_segments'][-1];q=current['source_segments'][0]
 if previous['speaker']!=current['speaker']:reasons.append('speaker_turn')
 if previous['voice']!=current['voice']:reasons.append('voice_boundary')
 if any(x['timing_locked'] for row in [*group,current] for x in row['items']):reasons.append('timing_lock')
 if q['id'] in forced:reasons.append('canonical_forced_split')
 if _STRONG_END.search(previous['items'][-1]['spoken_text']) or _STRONG_END.search(p['source_text']):reasons.append('strong_sentence_boundary')
 gap=current['original_start_ms']-previous['original_end_ms']
 if gap < -120 or gap>350:reasons.append('not_small_gap')
 span=max(r['original_end_ms'] for r in [*group,current])-min(r['original_start_ms'] for r in [*group,current])
 if span>8000:reasons.append('utterance_span_over_8000')
 return reasons,gap,span
async def synth(text,voice,stem):
 raw=WORK/f'{stem}.mp3';trim=WORK/f'{stem}.trim.mp3';start=time.monotonic()
 await asyncio.wait_for(generate_voice(text,voice_id=voice,output_path=raw,speed=1.0),timeout=45)
 raw_ms=round(probe_duration(raw,0)*1000);assert raw_ms>0
 trim.write_bytes(raw.read_bytes())
 trimmed=await asyncio.to_thread(_trim_edge_tts_boundary_silence_sync,trim,raw_ms)
 row={'text':text,'voice':voice,'rate':'+0%','raw_ms':raw_ms,'trimmed_ms':trimmed,'raw_path':str(raw),'trimmed_path':str(trim),'raw_sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),'seconds':time.monotonic()-start}
 append('tts.jsonl',row);return row

def physical_silence(source):
 cmd=['ffmpeg','-hide_banner','-nostats','-threads','2','-i',source,'-map','0:a:0','-af','silencedetect=noise=-35dB:d=0.06','-f','null',os.devnull]
 result=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
 assert result.returncode==0,result.stderr[-300:]
 events=[(kind,float(v)*1000) for kind,v in re.findall(r'silence_(start|end):\s*(-?\d+(?:\.\d+)?)',result.stderr)]
 intervals=[];start=None
 for kind,value in events:
  if kind=='start':start=max(0,value)
  elif start is not None:intervals.append([start,max(start,value)]);start=None
 append('silence-probes.jsonl',{'source':source,'threshold_db':-35,'min_silence_s':0.06,'exit':result.returncode,'intervals_ms':intervals})
 return intervals

def intersection_ms(intervals,start,end):return sum(max(0,min(b,end)-max(a,start)) for a,b in intervals)
async def main():
 start=time.monotonic();videos=[];boundaries=[];pairs=[];e_rows=[];blockers=[]
 for vpos,video in enumerate(CORPUS['videos']):
  sid=video['source_id'];rows=[r for r in CORPUS['chunks'] if r['source_id']==sid]
  source=next(s for s in CORPUS['dataset']['sources'] if s['source_id']==sid)
  silence=await asyncio.to_thread(physical_silence,source['source_path'])
  config=DubbingConfig(**video['config']);plan=[PlanItem(**x) for x in video['plan']]
  achunks=[DubbingChunk(**x) for x in video['chunks']];canonical_before=copy.deepcopy(achunks)
  groups=[];group=[rows[0]]
  for prev,current in zip(rows,rows[1:]):
   reason,gap,span=boundary_reason(prev,current,group,set(video['forced_splits']))
   boundaries.append({'source_id':sid,'left_chunk_id':prev['chunk_id'],'right_chunk_id':current['chunk_id'],'reason':reason,'gap_ms':gap,'proposed_span_ms':span,'merge':not reason})
   if reason:groups.append(group);group=[current]
   else:group.append(current)
  groups.append(group)
  bgroups=[];live_replacements={};pair_calls=0
  for gpos,group in enumerate(groups):
   indexes=[i for row in group for i in next(c for c in achunks if c.id==row['chunk_id']).item_indexes]
   text=' '.join(row['spoken_text'] for row in group)
   assert text.split()==[tok for row in group for tok in row['spoken_text'].split()]
   duration=sum(row['recorded_trimmed_ms'] for row in group)
   if len(group)>1:
    pair={'source_id':sid,'canonical_chunk_ids':[r['chunk_id'] for r in group],'cue_ids':[i for row in group for i in row['segment_ids']],'voice':group[0]['voice'],'original_start_ms':min(r['original_start_ms'] for r in group),'original_end_ms':max(r['original_end_ms'] for r in group),'lexical_tokens_identical':True,'timing_lock_crossed':False,'speaker_turn_crossed':False}
    try:
     separate=[]
     for row in group:
      sample=await synth(row['spoken_text'],row['voice'],f'v{vpos}_g{gpos}_a{row["chunk_id"]}')
      pair_calls+=1;separate.append(sample);live_replacements[row['chunk_id']]=sample['trimmed_ms']
     combined=await synth(text,group[0]['voice'],f'v{vpos}_g{gpos}_b');pair_calls+=1
     duration=combined['trimmed_ms'];pair.update(status='LIVE_PAIRED',a=separate,b=combined,a_total_trimmed_ms=sum(s['trimmed_ms'] for s in separate),b_trimmed_ms=duration,calls_saved_per_run=len(group)-1)
     words_path=Path(combined['raw_path']+'.words.json')
     pair['provider_word_boundaries_available']=words_path.exists()
     pair['mapping_verification']='Cue lexical mapping only; no production timestamp map/forced alignment; actual mapping not integrated.'
    except Exception as exc:
     pair.update(status='BLOCKED',error_type=type(exc).__name__,error=str(exc));blockers.append(pair)
    pairs.append(pair);append('paired-results.jsonl',pair)
   bgroups.append(DubbingChunk(id=gpos+1,item_indexes=indexes,start_ms=min(r['original_start_ms'] for r in group),end_ms=max(r['original_end_ms'] for r in group),speaker_id=group[0]['speaker'],voice_id=group[0]['voice'],actual_duration_ms=duration))
  for c in achunks:
   if c.id in live_replacements:c.actual_duration_ms=live_replacements[c.id]
  allocate_silence(plan,achunks,config,timeline_end_ms=video['duration_ms'])
  bplan=copy.deepcopy(plan);allocate_silence(bplan,bgroups,config,timeline_end_ms=video['duration_ms'])
  def metrics(chunks):
   return {'groups':len(chunks),'total_trimmed_ms':sum(c.actual_duration_ms for c in chunks),'hard_overflow_groups':sum(c.actual_duration_ms/c.available_ms>config.emergency_speed_max for c in chunks),'soft_overflow_groups':sum(config.speed_max<c.actual_duration_ms/c.available_ms<=config.emergency_speed_max for c in chunks),'above_quality_groups':sum(c.actual_duration_ms/c.available_ms>config.speed_max for c in chunks),'required_speed':[max(1,c.actual_duration_ms/c.available_ms) for c in chunks]}
  videos.append({'source_id':sid,'a':metrics(achunks),'b':metrics(bgroups),'changed_groups':sum(len(g)>1 for g in groups),'extra_tts_calls':pair_calls,'comparison_basis':'Fresh paired A/B for changed groups; exact stored audio for unchanged groups. Not a full pipeline rerun.','utterance_cap_ms':8000,'only_grouping_factor':'Remove item-count cap at allowed continuation boundaries, retain source8s gap/speaker/voice/lock/strong boundary limits.'})
  # E evaluates original canonical recorded durations, not B.
  for pos,(chunk,row) in enumerate(zip(canonical_before,rows)):
   speed=row['required_speed_original']
   if speed<=config.speed_max:continue
   prev=rows[pos-1] if pos else None;nxt=rows[pos+1] if pos+1<len(rows) else None
   left=0 if prev is None else prev['original_end_ms'];right=video['duration_ms'] if nxt is None else nxt['original_start_ms']
   gap_before=max(0,row['original_start_ms']-left);gap_after=max(0,right-row['original_end_ms'])
   reserve_before=min(gap_before,350) if prev else 0;reserve_after=min(gap_after,350) if nxt else 0
   reasons=[]
   if any(x['start_locked'] or x['end_locked'] or x['timing_locked'] for x in row['items']):reasons.append('timing_lock')
   if gap_before or gap_after:reasons.append('configured_source_gap_reserve')
   if gap_before==0 or gap_after==0:reasons.append('neighbor_speech_no_gap')
   if gap_before>1500 or gap_after>1500:reasons.append('max_movement_policy')
   if row['allocated_window_ms']>=max(8000,row['original_window_ms']):reasons.append('max_span_policy')
   if (prev and prev['speaker']!=row['speaker']) or (nxt and nxt['speaker']!=row['speaker']):reasons.append('speaker_boundary_blocks_grouping_not_allocator')
   e_rows.append({'source_id':sid,'chunk_id':chunk.id,'source_text':row['source_text'],'spoken_text':row['spoken_text'],'voice':row['voice'],'raw_before_trim_ms':None,'trimmed_ms':row['recorded_trimmed_ms'],'original_window_ms':row['original_window_ms'],'allocated_window_ms':row['allocated_window_ms'],'original_required_speed_raw':None,'required_speed_after_trim_original_window':speed,'required_speed_after_current_allocator':row['required_speed_allocated'],'allocation_gained_ms':row['allocated_window_ms']-row['original_window_ms'],'borrowed_before_ms':row['borrowed_before_ms'],'borrowed_after_ms':row['borrowed_after_ms'],'source_gap_before_ms':gap_before,'source_gap_after_ms':gap_after,'reserve_before_ms':reserve_before,'reserve_after_ms':reserve_after,'acoustic_silence_before_ms_at_minus35dB':intersection_ms(silence,left,row['original_start_ms']),'acoustic_silence_after_ms_at_minus35dB':intersection_ms(silence,row['original_end_ms'],right),'policy_contributors':reasons,'physical_measurement_limit':'-35dB silence detection is an acoustic proxy, not proof unused gaps can be consumed without source prosody change.'})
  save('results.json',{'videos':videos,'boundary_decisions':boundaries,'pairs':pairs,'e_overflows':e_rows,'blockers':blockers,'seconds':time.monotonic()-start})
 result={'videos':videos,'boundary_decisions':boundaries,'pairs':pairs,'e_overflows':e_rows,'blockers':blockers,'seconds':time.monotonic()-start,'auto_complete_video_rate':None,'auto_complete_reason':'No downstream production render/QC run for alternative grouping; only timing feasibility measured.','naturalness':'Not manually listened.','allocator':'Production allocate_silence only, no new allocator.'}
 save('results.json',result)
 print(json.dumps({'videos':len(videos),'eligible_changed_groups':len(pairs),'paired_live':sum(p['status']=='LIVE_PAIRED' for p in pairs),'blockers':len(blockers),'e_overflows':len(e_rows),'seconds':result['seconds'],'video_metrics':videos},ensure_ascii=False,indent=2))
 return int(bool(blockers))
if __name__=='__main__':
 raise SystemExit(asyncio.run(main()))