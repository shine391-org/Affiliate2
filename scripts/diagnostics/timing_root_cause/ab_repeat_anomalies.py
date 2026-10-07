"""Repeat extreme rate outliers without overwriting original measurements."""
import asyncio,hashlib,json,os,secrets,shutil,sys,time
from pathlib import Path
from cryptography.fernet import Fernet
ROOT=Path(os.environ['TIMING_ARTIFACTS_ROOT']).resolve();OUT=ROOT/'ab/parent-rate-repeats';OUT.mkdir(exist_ok=True)
os.environ.update(VIRALMINT_DATA_DIR=str(ROOT/'ab-parent-isolation'),SECRET_KEY=secrets.token_urlsafe(48),ENCRYPTION_KEY=Fernet.generate_key().decode())
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'app'))
import edge_tts
from backend.services.edge_tts_service import _add_natural_pauses
from backend.services.localization_service import _trim_edge_tts_boundary_silence_sync
from backend.services.video_utils import probe_duration
async def main():
 corpus=json.loads((ROOT/'chunk-corpus.json').read_text(encoding='utf-8'))
 cases=[r for r in corpus['chunks'] if (r['source_id'].startswith('legacy:25') and r['chunk_id']==40) or (r['source_id']=='commons:72844427' and r['chunk_id']==7)]
 assert len(cases)==2
 rows=[];started=time.monotonic()
 for i,r in enumerate(cases):
  for repeat in [1,2]:
   for rate in ['+0%','+30%']:
    stem=f'case{i}_repeat{repeat}_rate{rate[1:-1]}';raw=OUT/(stem+'.mp3');trim=OUT/(stem+'.trim.wav');events=OUT/(stem+'.words.jsonl')
    row={'source_id':r['source_id'],'chunk_id':r['chunk_id'],'text':r['spoken_text'],'voice':r['voice'],'rate':rate,'repeat':repeat,'raw_path':str(raw),'trimmed_path':str(trim)}
    try:
     c=edge_tts.Communicate(_add_natural_pauses(r['spoken_text']),r['voice'],rate=rate,pitch='+0Hz',boundary='WordBoundary')
     await asyncio.wait_for(c.save(str(raw),str(events)),45)
     raw_ms=round(probe_duration(raw)*1000);raw_sha=hashlib.sha256(raw.read_bytes()).hexdigest();shutil.copy2(raw,trim)
     trimmed_ms=_trim_edge_tts_boundary_silence_sync(trim,raw_ms)
     row.update(raw_ms=raw_ms,trimmed_ms=trimmed_ms,raw_sha256=raw_sha,trimmed_sha256=hashlib.sha256(trim.read_bytes()).hexdigest(),word_events=str(events),status='LIVE')
    except Exception as e:row.update(status='FAIL',error=str(e))
    with (OUT/'measurements.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(json.dumps(row,ensure_ascii=False),flush=True);rows.append(row)
 paired=[]
 for r in cases:
  for repeat in [1,2]:
   p=[x for x in rows if x['source_id']==r['source_id'] and x['repeat']==repeat]
   if all(x['status']=='LIVE' for x in p):paired.append({'source_id':r['source_id'],'chunk_id':r['chunk_id'],'repeat':repeat,'baseline_ms':p[0]['trimmed_ms'],'rate30_ms':p[1]['trimmed_ms'],'reduction_pct':round(100*(1-p[1]['trimmed_ms']/p[0]['trimmed_ms']),2)})
 report={'rows':rows,'paired':paired,'elapsed_s':round(time.monotonic()-started,3),'scope':'eight new provider calls preserving original anomalous A evidence; no listening/recognition fidelity acceptance'}
 (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(paired,ensure_ascii=False,indent=2))
 return all(x['status']=='LIVE' for x in rows)
if __name__=='__main__':sys.exit(0 if asyncio.run(main()) else 1)