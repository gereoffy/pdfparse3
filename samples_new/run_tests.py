#! /usr/bin/python3
# A samples_new/ teszt-pdf-jeinek ellenorzese az elvart eredmenyekkel (README 10.x javitasok regresszios tesztje),
# plusz nehany kozvetlen egysegteszt a lexerre es a segedfuggvenyekre.
# Hasznalat: python3 samples_new/run_tests.py [-v]      (kilepesi kod: 0 = minden OK)

import sys,os,io,contextlib,random
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,os.path.dirname(HERE))
import pdfparse3 as P

verbose='-v' in sys.argv
fails=0

def check(name,cond,info=""):
    global fails
    if not cond: fails+=1
    if not cond or verbose: print("%s  %s  %s"%("OK  " if cond else "FAIL",name,info))

def load(name): return open(os.path.join(HERE,name),'rb').read()

def run(name,validate=False,debug=False):
    buf=io.StringIO()
    pdf=P.PDFParser(load(name),debug=debug,validate=validate)
    with contextlib.redirect_stdout(buf): pdf.parse()
    return pdf,buf.getvalue()

def msgs(pdf): return [m for w,m in pdf.errors]

# 00: hibatlan pdf
pdf,out=run('00_clean_minimal.pdf')
check('00 no errors',pdf.errors==[],str(pdf.errors))
check('00 pages',pdf.pagecnt==1 and pdf.pagenum==1,"%d/%s"%(pdf.pagecnt,pdf.pagenum))
check('00 xref ok',len(pdf.xref)==3 and not pdf.badxref,str(pdf.xref))

# 01: objstm utolso tokenje (10.1)
pdf,out=run('01_objstm_last_token.pdf',debug=True)
check('01 last token intact',"-> #6: [612]" in out,[l for l in out.splitlines() if 'OBJSTREAM' in l][-1:])
check('01 no errors',pdf.errors==[],str(pdf.errors))

# 02: startxref ertek nelkul (10.2)
pdf,out=run('02_startxref_no_value.pdf')
check('02 targeted error',any(m.startswith('XREF: invalid startxref value') for m in msgs(pdf)),str(msgs(pdf)))
check('02 no exception',not any('exception' in m for m in msgs(pdf)),str(msgs(pdf)))
check('02 xref found by fallback',len(pdf.xref)==3 and not pdf.badxref,str(pdf.xref))
check('02 weight',sum(w for w,m in pdf.errors)<=2,str(pdf.errors))

# 03: hivatkozott /Length, tomoritetlen beagyazott pdf (10.3)
pdf,out=run('03_indirect_length_embedded_pdf.pdf')
inner=[c for c in pdf.content if c[1]=='inner.pdf']
check('03 attachment name',len(inner)==1,str([n for d,n in pdf.content]))
import re
declared=int(re.search(rb'6 0 obj\s*(\d+)\s*endobj',load('03_indirect_length_embedded_pdf.pdf')).group(1))
check('03 attachment complete',inner and len(inner[0][0])==declared and bytes(inner[0][0]).rstrip().endswith(b'%%EOF') and inner[0][0].count(b'endstream')==2,"%d/%d bytes"%(len(inner[0][0]) if inner else 0,declared))
check('03 no errors',pdf.errors==[],str(pdf.errors))

# 04: ket csatolmany, Filespec elobb / utobb (10.4)
for name in ('04a_attachments_filespec_first.pdf','04b_attachments_stream_first.pdf','04c_attachments_indirect_ef.pdf'):
    pdf,out=run(name)
    got=sorted((bytes(d),n) for d,n in pdf.content)
    check(name[:3]+' names',got==[(b'AAAAA','alpha.txt'),(b'BBBBB','beta.txt')],str(got))
    check(name[:3]+' no errors',pdf.errors==[],str(pdf.errors))

# 05: rossz startxref, az egyetlen ASCII "xref" a korabbi "startxref"-ben (10.5)
pdf,out=run('05_bad_startxref_xrefstream_prev_section.pdf')
check('05 no false table error',not any('subsection' in m for m in msgs(pdf)),str(msgs(pdf)))
check('05 invalid offset reported',any(m.startswith('XREF: invalid startxref offset') for m in msgs(pdf)),str(msgs(pdf)))
check('05 objects scanned',sorted(pdf.dom)==[1,2,3,4,5],str(sorted(pdf.dom)))

# 06: szemet a header elott (10.6)
pdf,out=run('06_junk_before_header.pdf')
check('06 base',pdf.base==8,str(pdf.base))
check('06 startxref adjusted',pdf.startxref and pdf.startxref[0]==pdf.find_last_xref(pdf.startxref[1]),str(pdf.startxref))
check('06 only JUNK error',[m[:4] for m in msgs(pdf)]==['JUNK'] and not pdf.badxref,str(msgs(pdf)))

# 07: validate mod, rossz offsetek -> walk_xref az obj_starts-bol (10.7)
pdf,out=run('07_validate_bad_xref_offsets.pdf',validate=True)
check('07 bad entries detected',sorted(pdf.badxref)==[3,4],str(pdf.badxref))
check('07 all objects walked',sorted(pdf.dom)==[1,2,3,4],str(sorted(pdf.dom)))
check('07 js found',[n for d,n in pdf.content]==['pdfstream.js'],str(pdf.content))
check('07 single summary error',msgs(pdf)==['XREF: 2/4 entries point to wrong positions'],str(msgs(pdf)))

# 08: LZW /EarlyChange 0 zaro kod nelkul + alap LZW zaro koddal (10.9)
pdf,out=run('08_lzw_earlychange0_no_eod.pdf')
payload=load('08_payload.bin')
got={n:bytes(d) for d,n in pdf.content}
check('08 early0/no-eod complete',got.get('early0_noeod.bin')==payload,"%s bytes"%len(got.get('early0_noeod.bin',b'')))
check('08 early1/eod complete',got.get('early1_eod.bin')==payload,"%s bytes"%len(got.get('early1_eod.bin',b'')))
check('08 uncertain-length/no-eod content',got.get('noeod_uncertain.bin',b'').startswith(payload),"%s bytes"%len(got.get('noeod_uncertain.bin',b'')))
check('08 only the uncertain one is an error',len(pdf.errors)==1 and pdf.errors[0][1].startswith('STREAM: decoding error in obj #8: LZW: no EOD code and the stream length is uncertain'),str(pdf.errors))
check('08 missing EOD is a note',any('obj #4: LZW: no EOD code' in l for l in out.splitlines()),str([l for l in out.splitlines() if 'LZW' in l]))

# 09: objstm fejlec nem offset-sorrendben (10.10): mindket belso obj megvan, nincs hamis hiba
pdf,out=run('09_objstm_unsorted_header.pdf',debug=True)
check('09 both objects parsed',all(("-> #%d: ['<', b'/Type', b'/Page'"%o) in out for o in (5,6)),str([l[:80] for l in out.splitlines() if 'OBJSTREAM #4' in l]))
check('09 pages counted',pdf.pagecnt==3,str(pdf.pagecnt))
check('09 no errors',pdf.errors==[],str(pdf.errors))

# egysegtesztek
def tok(s): return P.parse_pdf_obj(s,0,len(s),err=lambda m,n=1:None)[1]
check('lexer token at end of buffer',(tok(b'123'),tok(b'true'),tok(b'R'),tok(b'12 0 R'))==([123],[b'true'],[b'R'],[12,0,b'R']),str((tok(b'123'),tok(b'true'),tok(b'R'))))
if hasattr(P.PDFParser,'counts_upto'):
    random.seed(1)
    dd=bytes(random.choice(b'\r\n\r\nab') for _ in range(5000))
    pdf=P.PDFParser(dd)
    ps=sorted(random.sample(range(0,5000),200))
    for needle in (b'\r\n',b'\n'):
        c=pdf.counts_upto(needle,ps)
        check('counts_upto %r'%needle,all(c[p]==dd.count(needle,0,p) for p in ps))
if hasattr(P.PDFParser,'rfind_xref'):
    d=b'xref 0 1\nstartxref\n5\nstartxref\n9'
    check('rfind_xref skips startxref',P.PDFParser(d).rfind_xref(0,len(d))==0)

print("%s: %d failed"%("ALL OK" if not fails else "FAILED",fails))
sys.exit(1 if fails else 0)
