#! /usr/bin/env pypy3

# Tomeges PDF vizsgalat a pdfparse3.py-val (vagy pdfparse.py), JSONL naplo a kesobbi elemzeshez.
# Python 3.8+ (pypy3 is).
#
#   pdfscan.py scan  [-j N] [-o scan.jsonl.gz] [--files files.txt] [--resume] [--timeout 120] [--max-size 500] [--mode both|validate|content] DIR|FILE ...
#   pdfscan.py report scan.jsonl.gz [...]  [-n 5] [--list KATEGORIA] [--dedup]
#   pdfscan.py pack files.txt ids.txt out.tgz   az id-khoz tartozo file-ok becsomagolasa KONYVTAR NELKUL, <id>_<nev>
#                                           neven (a csomagba nem kerulhet utvonal). Ezt kell hasznalni mintakereshez!
#   pdfscan.py paths [-0] files.txt ids.txt az id-khoz tartozo teljes utvonalak (csak a szerveren hasznalhato, a
#                                           kimenete tartalmazza az utvonalakat; tar-hoz NEM: a tar eltarolja a konyvtarakat)
#
# scan:
#   -j N          parhuzamos processzek szama (alapbol a CPU-k szama)
#   -o FILE       kimenet (.gz vegu nev eseten gzip). Alapbol: pdfscan-EEEEHHNN-OOPP.jsonl.gz
#   --files FILE  az id -> teljes utvonal lista (alapbol files.txt). A naplo NEM tartalmaz utvonalat (a file-ok helye
#                 nem publikus), csak az id-t; ez a lista a szerveren marad, ebbol keresheto vissza a file.
#   --resume      a mar a kimenetben levo file-okat kihagyja (megszakadt futas folytatasa, hozzafuzes)
#   --timeout S   file-onkenti idokorlat masodpercben (alapbol 120)
#   --max-size MB ennel nagyobb file-t nem vizsgal, csak naplozza (alapbol 500)
#   --mode        validate: hibaellenorzes (xref bejarassal), content: tartalom kinyeres (linearis olvasas),
#                 both: mindketto (alapbol)
#   --all         nem csak a .pdf kiterjesztesu file-okat vizsgalja (a nem pdf-ek "notpdf" statusszal kerulnek be)
#   --follow-dirs a konyvtarra mutato symlinkeket is koveti (a file-ra mutatokat mindig)
#   --no-text     content modban ne nyerje ki az elso oldal szoveget (pdf2str). Alapbol kinyeri, de a naploba
#                 csak a meroszamai kerulnek (karakter / szo / sor, ido, kivetel), maga a szoveg NEM (szemelyes adat)
#   --no-dedup    az azonos tartalmu (sha256) file-okat is mind megvizsgalja. Alapbol egy tartalmat csak egyszer
#                 vizsgal: a tobbi peldany "duplicate" statusszal kerul a naplo es a files.txt-be (a sha256-hoz
#                 igy is vegig kell olvasni, de a parse kimarad).
#
# A naplo soronkent egy JSON objektum (file-onkent):
#   id (az utvonal sha256-janak eleje), name (a file neve, konyvtar nelkul), size, sha256 (tartalom), status (ok / notpdf / timeout / crash / toobig / readerror / duplicate), time,
#   validate: {errcnt, errors:[[suly,uzenet],...]}   content: {errcnt, errors, items:[{name,size,sha256,js}], out,
#   text:{chars,words,lines,time[,error]}}
#   'out' a parser kimenetenek eleje (csak ha volt hiba), 'tb' a traceback crash eseten.
#
# files.txt: soronkent  id <TAB> sha256 <TAB> teljes utvonal   (a \, TAB, sorvege \\, \t, \n-kent)
#
# report: osszesites a naplobol: statuszok, a file-ok osztalya modonkent (az errcnt pontszam alapjan: 0 = hibatlan,
# 1..9 = figyelmeztetes: nem szabvanyos, de hasznalhato, >=10 = serult, 99 = nem pdf), a serult file-ok a fo (elso)
# serules-diagnozis szerint, a diagnozisok sulyuk szerint (a JUNK / TRANSFER lehet serules es figyelmeztetes is),
# hibatipusok (a szamokat N-re cserelve), leglassabb file-ok, es kategoriankent nehany pelda file id-ja (ezeket erdemes
# letolteni, a 'paths' paranccsal az utvonaluk).
#   --dedup   az azonos tartalmu (sha256) file-okat csak egyszer szamolja (tobb lemez / masolatok)
#   --list K  csak a K kategoria (hibatipus / diagnozis resz-szovege, vagy DAMAGED / WARNING: a validate mod szerint
#             serult / figyelmeztetes osztalyu file-ok, NOTEXT / TEXTERROR: nincs szoveg / kivetel a szoveg
#             kinyeresben) pelda id-jai, soronkent (a 'pack'-nek atadhato ids.txt)

import os
import sys
import io
import re
import gzip
import json
import time
import signal
import hashlib
import traceback
import contextlib
import collections
import multiprocessing
import multiprocessing.managers

HERE=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,HERE)
try:
    import pdfparse3 as pdfparse   # (a repo neve; a szerveren lehet meg pdfparse.py)
except ImportError:
    import pdfparse
try:
    import pdf2str   # (az elso oldal szovege, content modban; ha nincs meg, kimarad)
except ImportError:
    pdf2str=None

MAX_ERRORS=50      # file-onkent ennyi hibauzenetet naplozunk (az errcnt mindig a teljes lista pontszama, pdfparse.score)
DAMAGE=getattr(pdfparse,'DAMAGE',10)   # ettol a sulytol serules (kisebb: figyelmeztetes), l. pdfparse.score
DIAGS=('TRANSFER','TRUNCATED','JUNK','GAP','CORRUPT','ENCODED')   # a hibalista elejen allo magyarazo diagnozisok
MAX_MSG=300        # egy uzenet max hossza
MAX_OUT=4000       # a parser kimenetebol ennyit naplozunk (csak hibas file-nal)

# a file azonositoja a naplokban: az utvonal sha256-janak eleje (az utvonal maga nem kerul a naplokba)
def path_id(fn):
    return hashlib.sha256(os.fsencode(fn)).hexdigest()[:16]

def esc(s):
    return s.replace('\\','\\\\').replace('\t','\\t').replace('\n','\\n').replace('\r','\\r')

def unesc(s):
    return re.sub(r'\\(.)',lambda m:{'t':'\t','n':'\n','r':'\r'}.get(m.group(1),m.group(1)),s)

def open_out(fn,mode):
    if fn.endswith('.gz'): return gzip.open(fn,mode+'t',encoding='utf-8')
    return open(fn,mode,encoding='utf-8')

def find_files(args,all_files,follow=False):
    seen=set()   # a mar bejart konyvtarak (valodi utvonal), hogy a korkoros symlink ne okozzon vegtelen ciklust
    for a in args:
        if os.path.isfile(a):
            yield a
            continue
        for root,dirs,files in os.walk(a,followlinks=follow):
            if follow:
                rp=os.path.realpath(root)
                if rp in seen:
                    dirs[:]=[]
                    continue
                seen.add(rp)
            dirs.sort()
            for f in sorted(files):
                if all_files or f.lower().endswith('.pdf'): yield os.path.join(root,f)

class Timeout(BaseException): pass   # nem Exception: a parser 'except Exception' agai ne nyeljek el
def on_alarm(*a): raise Timeout()

def ignore_sigint():
    signal.signal(signal.SIGINT,signal.SIG_IGN)

SEEN=None   # a mar vizsgalt tartalmak (sha256 eleje), a processzek kozott megosztva (multiprocessing.Manager)

def init_worker(seen=None):
    global SEEN
    SEEN=seen
    signal.signal(signal.SIGALRM,on_alarm)
    signal.signal(signal.SIGINT,signal.SIG_IGN)   # a Ctrl-C-t a fo processz kezeli

def run_mode(data,validate,text=False):
    out=io.StringIO()
    tx=None
    with contextlib.redirect_stdout(out):
        # (mint a pdfparse.parse_pdf, de a parser megmarad a szoveg kinyereshez: nem kell ketszer feldolgozni)
        p=pdfparse.PDFParser(data,False,validate)
        if p.parse(): c,e,errs=p.content,pdfparse.score(p.errors),p.errors
        else: c,e,errs=None,99,[(99,"not pdf")]
        if text and c is not None:
            t=time.time()
            terr=[]
            txt=pdf2str.first_page_text(p,errors=terr) or ''
            # (a szoveg maga nem kerul a naploba: szemelyes adat lehet)
            tx={'chars':len(txt),'words':len(txt.split()),'lines':txt.count('\n')+1 if txt else 0,'time':round(time.time()-t,3)}
            if terr: tx['error']=terr[0][-MAX_MSG:]
    r={'errcnt':e,'errors':[[w,m[:MAX_MSG]] for w,m in (errs or [])[:MAX_ERRORS]]}
    if tx is not None: r['text']=tx
    if errs and len(errs)>MAX_ERRORS: r['errors_total']=len(errs)
    if not validate and c:
        r['items']=[{'name':n if isinstance(n,str) else repr(n),'size':len(d),'sha256':hashlib.sha256(bytes(d)).hexdigest(),
                     'js':n=='pdfstream.js'} for d,n in c if d is not None]
    if e and e!=99: r['out']=out.getvalue()[:MAX_OUT]
    return r,c is None

def scan_file(job):
    fn,opts=job
    # a file neve bekerulhet a naploba, a konyvtar (melyik ugyfel, melyik projekt) nem.
    # A _path csak a files.txt-be kerul, a naplo irasa elott kivesszuk.
    rec={'id':path_id(fn),'name':os.fsencode(os.path.basename(fn)).decode('utf-8','replace'),'_path':fn}
    t=time.time()
    try:
        rec['size']=os.path.getsize(fn)
        if rec['size']>opts['max_size']:
            rec['status']='toobig'
            return rec
        with open(fn,'rb') as f: data=f.read()
    except Exception as ex:
        rec['status']='readerror'
        rec['error']='%s errno=%s'%(type(ex).__name__,getattr(ex,'errno',None))   # (a hibauzenetben benne lenne az utvonal)
        return rec
    rec['sha256']=hashlib.sha256(data).hexdigest()
    # ugyanez a tartalom mar sorra kerult (egy masik processzben is): nem vizsgaljuk ujra
    if SEEN is not None and SEEN.setdefault(rec['sha256'][:24],rec['id'])!=rec['id']:
        rec['status']='duplicate'
        rec['time']=round(time.time()-t,3)
        return rec
    signal.alarm(opts['timeout'])
    try:
        notpdf=False
        if opts['mode'] in ('both','validate'): rec['validate'],notpdf=run_mode(data,True)
        if opts['mode'] in ('both','content') and not notpdf: rec['content'],notpdf=run_mode(data,False,opts['text'])
        rec['status']='notpdf' if notpdf else 'ok'
    except Timeout:
        rec['status']='timeout'
    except BaseException:
        rec['status']='crash'
        rec['tb']=traceback.format_exc()[-3000:]
    finally:
        signal.alarm(0)
    rec['time']=round(time.time()-t,3)
    return rec

def cmd_scan(argv):
    opts={'jobs':os.cpu_count() or 1,'out':time.strftime('pdfscan-%Y%m%d-%H%M.jsonl.gz'),'files':'files.txt','resume':False,
          'timeout':120,'max_size':500,'mode':'both','all':False,'follow':False,'dedup':True,'text':pdf2str is not None}
    paths=[]
    i=0
    while i<len(argv):
        a=argv[i]
        if a=='-j': opts['jobs']=int(argv[i+1]); i+=1
        elif a=='-o': opts['out']=argv[i+1]; i+=1
        elif a=='--files': opts['files']=argv[i+1]; i+=1
        elif a=='--resume': opts['resume']=True
        elif a=='--timeout': opts['timeout']=int(argv[i+1]); i+=1
        elif a=='--max-size': opts['max_size']=int(argv[i+1]); i+=1
        elif a=='--mode': opts['mode']=argv[i+1]; i+=1
        elif a=='--all': opts['all']=True
        elif a=='--follow-dirs': opts['follow']=True
        elif a=='--no-dedup': opts['dedup']=False
        elif a=='--no-text': opts['text']=False
        else: paths.append(a)
        i+=1
    if not paths or opts['mode'] not in ('both','validate','content'):
        print("usage: pdfscan.py scan [-j N] [-o FILE] [--files FILE] [--resume] [--timeout S] [--max-size MB] [--mode both|validate|content] [--all] [--follow-dirs] [--no-dedup] [--no-text] DIR|FILE ...")
        print("(details at the top of pdfscan.py)")
        sys.exit(1)
    opts['max_size']*=1024*1024
    if not opts['resume'] and (os.path.exists(opts['out']) or os.path.exists(opts['files'])):
        # (kulonben egy uj futas a regi naplohoz fuzne, es osszekeveredne a ket eredmeny)
        print("output %s or %s already exists: use --resume to continue it, or another -o / --files"%(opts['out'],opts['files']),file=sys.stderr)
        sys.exit(1)
    done=set()
    seen_sha=set()
    if opts['resume'] and os.path.exists(opts['out']):
        try:
            with open_out(opts['out'],'r') as f:
                for l in f:
                    try:
                        r=json.loads(l)
                        done.add(r['id'])
                        if r.get('sha256') and r.get('status')!='duplicate': seen_sha.add(r['sha256'][:24])
                    except Exception: pass   # megszakadt utolso sor (vagy a meta sor)
        except (EOFError,OSError): pass     # csonka gzip vege
        print("resume: %d files already done"%(len(done)),file=sys.stderr,flush=True)
    print("listing files...",file=sys.stderr,flush=True)
    files=[fn for fn in find_files(paths,opts['all'],opts['follow']) if path_id(fn) not in done]
    print("%d files to scan, %d processes, output: %s"%(len(files),opts['jobs'],opts['out']),file=sys.stderr,flush=True)
    seen=None
    if opts['dedup']:
        # a mar vizsgalt tartalmak a processzek kozott megosztva. A manager processz unix socketen kommunikal: ahol ez
        # nem engedelyezett (pl. sandbox), deduplikacio nelkul folytatjuk (kulonben semmitmondo EOFError lenne).
        # (a manager processz ne haljon meg a Ctrl-C-tol, amig a fo processz lezarja a naplot)
        try:
            manager=multiprocessing.managers.SyncManager()
            manager.start(ignore_sigint)
            seen=manager.dict()
            seen.update(dict.fromkeys(seen_sha,'resume'))
        except (OSError,EOFError) as ex:
            print("WARNING: dedup disabled, the multiprocessing manager could not start: %s"%(repr(ex)),file=sys.stderr,flush=True)
            opts['dedup']=False
            seen=None
    t0=time.time()
    n=0
    stats=collections.Counter()
    with open_out(opts['out'],'a') as out, open(opts['files'],'a',encoding='utf-8',errors='surrogateescape') as flist:
        if not done:
            # (a parancssorbol csak a kapcsolok, a konyvtarak nem: azok utvonalak)
            out.write(json.dumps({'meta':{'started':time.strftime('%Y-%m-%d %H:%M:%S'),'roots':len(paths),'python':sys.version,
                'parser_sha256':hashlib.sha256(open(pdfparse.__file__,'rb').read()).hexdigest(),
                'opts':dict((k,v) for k,v in opts.items() if k not in ('out','files'))}})+'\n')
        pool=multiprocessing.Pool(opts['jobs'],init_worker,(seen,),maxtasksperchild=2000)
        try:
            for rec in pool.imap_unordered(scan_file,((fn,opts) for fn in files),chunksize=4):
                fn=rec.pop('_path')
                flist.write('%s\t%s\t%s\n'%(rec['id'],rec.get('sha256','-'),esc(fn)))
                out.write(json.dumps(rec,ensure_ascii=False)+'\n')
                n+=1
                stats[rec.get('status')]+=1
                if n%1000==0:
                    out.flush()
                    flist.flush()
                    el=time.time()-t0
                    print("%d/%d  %.0f files/s  ETA %.0f min  %s"%(n,len(files),n/el,(len(files)-n)/(n/el)/60,dict(stats)),file=sys.stderr,flush=True)
            pool.close()
        except KeyboardInterrupt:
            print("interrupted, %d files written (continue with --resume)"%(n),file=sys.stderr,flush=True)
            pool.terminate()
        pool.join()
    print("done: %d files in %.0f s  %s"%(n,time.time()-t0,dict(stats)),file=sys.stderr,flush=True)

# a hibauzenet tipusa: a szamok, hex ertekek, idezett adatok nelkul
def msg_kind(m):
    m=m.split('Traceback')[0]
    m=re.sub(r"b'.*|bytearray\(.*|\[.*","...",m)
    m=re.sub(r'0x[0-9A-Fa-f]+','N',m)
    m=re.sub(r'(?<![A-Za-z0-9])#?\d+','N',m)
    return m[:110].strip()

# peldakent: "id <TAB> nev" (az id-bol a szerveren a 'paths' paranccsal kaphato meg az utvonal)
def ex_name(r):
    if 'id' in r: return '%s\t%s'%(r['id'],r.get('name',''))
    return r.get('path')

def cmd_report(argv):
    nex=5
    dedup=False
    seen_sha=set()
    dups=0
    listcat=None
    files=[]
    i=0
    while i<len(argv):
        if argv[i]=='-n': nex=int(argv[i+1]); i+=1
        elif argv[i]=='--list': listcat=argv[i+1]; i+=1
        elif argv[i]=='--dedup': dedup=True
        else: files.append(argv[i])
        i+=1
    status=collections.Counter()
    kinds=collections.Counter()      # (mod, tipus) -> file-ok szama
    examples=collections.defaultdict(list)
    diag=collections.Counter()
    classes=collections.defaultdict(collections.Counter)   # mod -> osztaly -> file-ok szama
    damaged=collections.Counter()   # a serult file-ok (validate) a fo serules-diagnozis szerint
    texts=[]                        # (chars, ido) a content modban kinyert szovegekhez
    tslow=[]
    slow=[]
    total=0
    sizes=0
    for fn in files:
        with open_out(fn,'r') as f:
            try:
                for l in f:
                    try: r=json.loads(l)
                    except ValueError: continue
                    if 'meta' in r: continue
                    if dedup and r.get('sha256'):
                        if r['sha256'] in seen_sha:
                            dups+=1
                            continue
                        seen_sha.add(r['sha256'])
                    total+=1
                    sizes+=r.get('size',0)
                    st=r.get('status')
                    status[st]+=1
                    if st in ('crash','timeout','readerror','toobig'):
                        examples['STATUS '+st].append(ex_name(r))
                        if st=='crash': kinds[('crash',msg_kind(r.get('tb','').strip().split('\n')[-1]))]+=1
                    slow.append((r.get('time',0),ex_name(r)))
                    for mode in ('validate','content'):
                        v=r.get(mode)
                        if not v: continue
                        s=v['errcnt']
                        cl='notpdf' if s>=99 else 'clean' if s==0 else 'WARNING' if s<DAMAGE else 'DAMAGED'
                        classes[mode][cl]+=1
                        if mode=='validate' and cl in ('DAMAGED','WARNING'):
                            examples[cl].append(ex_name(r))
                            if cl=='DAMAGED':
                                main=[m for w,m in v['errors'] if DAMAGE<=w<99]
                                damaged[msg_kind(main[0])[:60] if main else '(sum of other errors)']+=1
                        seen=set()
                        for w,m in v['errors']:
                            k=msg_kind(m)
                            if k in seen: continue
                            seen.add(k)
                            kinds[(mode,k)]+=1
                            if len(examples[(mode,k)])<50: examples[(mode,k)].append(ex_name(r))
                            if mode=='validate' and m.split(':')[0] in DIAGS: diag[('damage ' if w>=DAMAGE else 'warning',msg_kind(m)[:60])]+=1
                    c=r.get('content') or {}
                    tx=c.get('text')
                    if tx:
                        texts.append(tx['chars'])
                        tslow.append((tx.get('time',0),ex_name(r)))
                        if not tx['chars']: examples['NOTEXT'].append(ex_name(r))
                        if tx.get('error'):
                            kinds[('text',msg_kind(tx['error'].strip().split('\n')[-1]))]+=1
                            examples['TEXTERROR'].append(ex_name(r))
                    if any(x.get('js') for x in c.get('items',[])): diag[('content','JavaScript found')]+=1
                    if any(not x.get('js') for x in c.get('items',[])): diag[('content','embedded file found')]+=1
            except (EOFError,OSError): pass
    if listcat:
        for k,v in examples.items():
            if (k==listcat) if listcat in ('DAMAGED','WARNING','NOTEXT','TEXTERROR') else (listcat in str(k)):
                for p in v: print(p.split('\t')[0])
        return
    print("files: %d, %.1f GB%s"%(total,sizes/1e9,", %d duplicates skipped"%(dups) if dedup else ""))
    print("status: %s"%(dict(status)))
    print("classes (0 = clean, 1..%d = warning: non-standard but usable, >=%d = damaged):"%(DAMAGE-1,DAMAGE))
    for mode in ('validate','content'):
        if classes[mode]: print("  %-8s %s"%(mode,'  '.join('%s %d'%(c,classes[mode][c]) for c in ('clean','WARNING','DAMAGED','notpdf') if classes[mode][c])))
    if texts:
        ts=sorted(texts)
        print("text (pdf2str, first page): %d files, with text %d (%.1f%%), no text %d, errors %d, chars median %d / p90 %d"%(
              len(ts),sum(1 for x in ts if x),100.0*sum(1 for x in ts if x)/len(ts),sum(1 for x in ts if not x),
              len(examples['TEXTERROR']),ts[len(ts)//2],ts[int(len(ts)*0.9)]))
    print("\ndamaged files (validate) by main diagnosis:")
    for k,v in damaged.most_common(): print("  %7d  %s"%(v,k))
    print("\ndiagnoses:")
    for (w,k),v in diag.most_common(): print("  %7d  %-8s %s"%(v,w,k))
    print("\nerror types (number of files):")
    for (mode,k),v in kinds.most_common(80): print("  %7d  %-8s %s"%(v,mode,k))
    print("\nslowest files:")
    for t,p in sorted(slow,reverse=True)[:10]: print("  %7.1fs  %s"%(t,p))
    if tslow:
        print("\nslowest text extractions:")
        for t,p in sorted(tslow,reverse=True)[:5]: print("  %7.1fs  %s"%(t,p))
    print("\nexamples per type (%d each):"%(nex))
    for (key) in sorted(examples,key=lambda k:-len(examples[k])):
        print("  %s"%(str(key)))
        for p in examples[key][:nex]: print("      %s"%(p))

# az id-k (ids.txt) -> utvonalak a files.txt-bol
def lookup_paths(flist,idfile):
    ids=set(l.strip().split('\t')[0] for l in open(idfile,encoding='utf-8') if l.strip())
    found={}
    with open(flist,encoding='utf-8',errors='surrogateescape') as f:
        for l in f:
            t=l.rstrip('\n').split('\t',2)
            if len(t)==3 and t[0] in ids and t[0] not in found: found[t[0]]=unesc(t[2])
    return ids,found

# mintakereshez: a file-ok becsomagolasa konyvtar nelkul (<id>_<nev>), hogy a csomagbol ne latszodjon, honnan vannak
def cmd_pack(argv):
    import tarfile
    if len(argv)<3:
        print("usage: pdfscan.py pack files.txt ids.txt out.tgz")
        sys.exit(1)
    ids,found=lookup_paths(argv[0],argv[1])
    with tarfile.open(argv[2],'w:gz') as tar:
        for i,p in sorted(found.items()):
            name='%s_%s'%(i,os.path.basename(p).replace('/','_'))
            ti=tar.gettarinfo(os.path.realpath(p),arcname=name)   # (symlink eseten a valodi file-t)
            ti.uid=ti.gid=0
            ti.uname=ti.gname=''
            with open(p,'rb') as f: tar.addfile(ti,f)
    print("%d files packed into %s%s"%(len(found),argv[2],", %d ids not found"%(len(ids)-len(found)) if len(found)<len(ids) else ""),file=sys.stderr)

# az id-khoz (ids.txt, soronkent egy, vagy a report --list kimenete) tartozo utvonalak a files.txt-bol
def cmd_paths(argv):
    sep='\n'
    if argv and argv[0]=='-0':
        sep='\0'
        argv=argv[1:]
    if len(argv)<2:
        print("usage: pdfscan.py paths files.txt ids.txt")
        sys.exit(1)
    ids=set(l.strip() for l in open(argv[1],encoding='utf-8') if l.strip())
    found=set()
    out=open(sys.stdout.fileno(),'w',encoding='utf-8',errors='surrogateescape',closefd=False)
    with open(argv[0],encoding='utf-8',errors='surrogateescape') as f:
        for l in f:
            t=l.rstrip('\n').split('\t',2)
            if len(t)==3 and t[0] in ids and t[0] not in found:
                found.add(t[0])
                out.write(unesc(t[2])+sep)
    out.flush()
    if len(found)<len(ids): print("%d ids not found"%(len(ids)-len(found)),file=sys.stderr)

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='scan': cmd_scan(sys.argv[2:])
    elif len(sys.argv)>1 and sys.argv[1]=='report': cmd_report(sys.argv[2:])
    elif len(sys.argv)>1 and sys.argv[1]=='paths': cmd_paths(sys.argv[2:])
    elif len(sys.argv)>1 and sys.argv[1]=='pack': cmd_pack(sys.argv[2:])
    else:
        print("usage: pdfscan.py scan [options] DIR|FILE ...   |   pdfscan.py report LOG.jsonl[.gz] ...   |   pdfscan.py pack files.txt ids.txt out.tgz")
        sys.exit(1)
