#! /usr/bin/python3
# A samples_new/ konyvtar teszt-pdf-jeinek (ujra)generalasa. Minden file egy-egy javitott hibat (README 10.x)
# vagy egy alap esetet fed le; az elvart eredmenyeket a run_tests.py ellenorzi.
# Hasznalat: python3 make_samples.py   (a sajat konyvtaraba ir)

import os

HERE=os.path.dirname(os.path.abspath(__file__))

# egy pdf osszeallitasa helyes xref tablaval. objs: [(oid, torzs), ...]  (a torzs az "N 0 obj" es "endobj" kozti resz)
def build(objs,root=1,version=b'1.4',startxref_value=None,eof=True,header_tail=b''):
    d=b'%PDF-'+version+header_tail+b'\n%\xe2\xe3\xcf\xd3\n'
    offs={}
    for oid,body in objs:
        offs[oid]=len(d)
        d+=b'%d 0 obj\n'%oid+body+b'\nendobj\n'
    xo=len(d)
    size=max(offs)+1
    d+=b'xref\n0 %d\n'%size+b'0000000000 65535 f \n'
    for oid in range(1,size):
        d+=b'%010d 00000 n \n'%offs[oid] if oid in offs else b'0000000000 65535 f \n'
    d+=b'trailer\n<</Size %d/Root %d 0 R>>\nstartxref\n'%(size,root)
    d+=(b'%d'%xo if startxref_value is None else startxref_value)+b'\n'
    if eof: d+=b'%%EOF\n'
    return d,offs,xo

def stream(dct,data):
    return dct[:-2]+b'/Length %d>>stream\n'%len(data)+data+b'\nendstream'

BASE=[(1,b'<</Type/Catalog/Pages 2 0 R>>'),(2,b'<</Type/Pages/Kids[3 0 R]/Count 1>>'),(3,b'<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>')]

def write(name,d):
    with open(os.path.join(HERE,name),'wb') as f: f.write(d)
    print("%-45s %6d bytes"%(name,len(d)))

# 00: hibatlan minimalis pdf (alap eset: 0 hiba)
clean,_,_=build(BASE)
write('00_clean_minimal.pdf',clean)

# 01: object stream, amelynek utolso obj-a egy szam, es az adat pont ott vegzodik (nincs zaro whitespace).
#     A lexer hibaja miatt 612 helyett 61 lett. (README 10.1)
hdr=b'5 0 6 8 '   # 8 byte: oid/offset parok
data=hdr+b'<</A 1>>612'
d,_,_=build(BASE+[(4,stream(b'<</Type/ObjStm/N 2/First %d>>'%len(hdr),data))])
write('01_objstm_last_token.pdf',d)

# 02: a startxref utan nincs ertek, rogton %%EOF (README 10.2)
d,_,_=build(BASE,startxref_value=b'')
write('02_startxref_no_value.pdf',d)

# 03: tomoritetlen csatolmany (egy teljes pdf, benne 'endstream'), hivatkozott /Length-el (README 10.3)
inner,_,_=build(BASE+[(4,stream(b'<</Foo/Bar>>',b'BT /F1 12 Tf (endstream inside) Tj ET'))])
d,_,_=build(BASE+[
    (4,stream(b'<</Type/EmbeddedFile/Subtype/application#2Fpdf>>',inner).replace(b'/Length %d>>'%len(inner),b'/Length 6 0 R>>')),
    (5,b'<</Type/Filespec/F(inner.pdf)/UF(inner.pdf)/EF<</F 4 0 R/UF 4 0 R>>>>'),
    (6,b'%d'%len(inner))])
write('03_indirect_length_embedded_pdf.pdf',d)

# 04a/04b: ket csatolmany; a Filespec-ek a streamek elott, ill. utan (README 10.4)
fs=[(6,b'<</Type/Filespec/F(alpha.txt)/EF<</F 4 0 R>>>>'),(7,b'<</Type/Filespec/UF(beta.txt)/EF<</UF 5 0 R>>>>')]
st=[(4,stream(b'<</Type/EmbeddedFile>>',b'AAAAA')),(5,stream(b'<</Type/EmbeddedFile>>',b'BBBBB'))]
d,_,_=build(BASE+fs+st); write('04a_attachments_filespec_first.pdf',d)
d,_,_=build(BASE+st+fs); write('04b_attachments_stream_first.pdf',d)
# 04c: az /EF hivatkozas (kulon obj-ban a << /F 4 0 R >> dict), a Filespec-ek a streamek utan, az EF dict-ek legvegen
fsr=[(6,b'<</Type/Filespec/F(alpha.txt)/EF 8 0 R>>'),(7,b'<</Type/Filespec/UF(beta.txt)/EF 9 0 R>>'),(8,b'<</F 4 0 R>>'),(9,b'<</UF 5 0 R/F 5 0 R>>')]
d,_,_=build(BASE+st+fsr); write('04c_attachments_indirect_ef.pdf',d)

# 05: elso szekcio ASCII xref-fel, majd incremental update xref stream-mel es rossz startxref-fel:
#     az egyetlen ASCII "xref" szo a korabbi "startxref"-ben van (README 10.5)
first,offs,xo1=build(BASE)
d=first
o4=len(d)
d+=b'4 0 obj\n<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Rotate 90>>\nendobj\n'
o5=len(d)
rows=b''.join(bytes([1])+off.to_bytes(2,'big')+b'\0' for off in (0,offs[1],offs[2],offs[3],o4,o5))
d+=b'5 0 obj\n'+stream(b'<</Type/XRef/W[1 2 1]/Size 6/Root 1 0 R/Prev %d>>'%xo1,rows)+b'\nendobj\n'
d+=b'startxref\n99999\n%%EOF\n'
write('05_bad_startxref_xrefstream_prev_section.pdf',d)

# 06: szemet a %PDF header elott, az offsetek a headerhez relativak (README 10.6)
write('06_junk_before_header.pdf',b'JUNKJUNK'+clean)

# 07: validate mod, rossz xref offsetek (2/4 bejegyzes 3 byte-tal eltolva), a JS az egyik rossz offsetu obj-ban:
#     a walk_xref-nek az obj_starts-bol kell megtalalnia (README 10.7)
objs=[(1,b'<</Type/Catalog/Pages 2 0 R/OpenAction 4 0 R>>'),(2,b'<</Type/Pages/Kids[3 0 R]/Count 1>>'),
      (3,b'<</Type/Page/Parent 2 0 R>>'),(4,b'<</S/JavaScript/JS(app.alert\\(1\\))>>')]
d,offs,xo=build(objs)
for oid in (3,4):
    d=d.replace(b'%010d 00000 n \n'%offs[oid],b'%010d 00000 n \n'%(offs[oid]+3))
write('07_validate_bad_xref_offsets.pdf',d)

# egyszeru LZW kodolo (a dekoder tesztjehez). early: /EarlyChange; eod=False: a zaro kod kihagyasa (egyes irok igy tesznek)
def lzw_encode(data,early=1,eod=True):
    table={bytes([i]):i for i in range(256)}
    nxt=258; bits=9
    codes=[(256,9)]
    w=b''
    for c in data:
        wc=w+bytes([c])
        if wc in table: w=wc; continue
        codes.append((table[w],bits))
        table[wc]=nxt; nxt+=1
        if nxt+early>(1<<bits) and bits<12: bits+=1
        w=bytes([c])
    if w: codes.append((table[w],bits))
    if eod: codes.append((257,bits))
    acc=n=0; out=bytearray()
    for code,b in codes:
        acc=(acc<<b)|code; n+=b
        while n>=8: out.append((acc>>(n-8))&0xFF); n-=8
    if n: out.append((acc<<(8-n))&0xFF)
    return bytes(out)

# 08: LZW /EarlyChange 0 zaro kod nelkul (obj 4), es alap EarlyChange 1 zaro koddal (obj 5): mindketto teljes,
#     obj 8: zaro kod nelkul, bizonytalan hosszal -> hiba (README 10.9). Az adat eleg hosszu, hogy a 9->10->11 bites szelessegvaltas is megtortenjen.
import random
random.seed(8)
payload=bytes(random.randrange(256) for _ in range(3000))
assert lzw_encode(payload,0,False)!=lzw_encode(payload,1,True)
d,_,_=build(BASE+[
    (4,stream(b'<</Type/EmbeddedFile/Filter/LZWDecode/DecodeParms<</EarlyChange 0>>>>',lzw_encode(payload,0,False))),
    (5,stream(b'<</Type/EmbeddedFile/Filter/LZWDecode>>',lzw_encode(payload,1,True))),
    (6,b'<</Type/Filespec/F(early0_noeod.bin)/EF<</F 4 0 R>>>>'),
    (7,b'<</Type/Filespec/F(early1_eod.bin)/EF<</F 5 0 R>>>>'),
    # zaro kod nelkul ES feloldhatatlan /Length (99 0 R): a stream vege bizonytalan -> a hianyzo EOD itt hiba
    (8,stream(b'<</Type/EmbeddedFile/Filter/LZWDecode>>',lzw_encode(payload,1,False)).replace(b'/Length %d>>'%len(lzw_encode(payload,1,False)),b'/Length 99 0 R>>')),
    (9,b'<</Type/Filespec/F(noeod_uncertain.bin)/EF<</F 8 0 R>>>>')])
write('08_lzw_earlychange0_no_eod.pdf',d)
with open(os.path.join(HERE,'08_payload.bin'),'wb') as f: f.write(payload)

# 09: object stream, amelynek fejlece nem offset szerinti sorrendben sorolja a parokat (6-os obj elobb, pedig az adatban
#     a 5-os az elso). Az obj hatarait a rendezett offsetekbol kell szamolni (README 10.10). Mindket obj /Type/Page.
o5=b'<</Type/Page/Parent 2 0 R/A 1>>  '; o6=b'<</Type/Page/Parent 2 0 R/B 2>>'
hdr=b'6 %d 5 0 '%len(o5)
d,_,_=build(BASE+[(4,stream(b'<</Type/ObjStm/N 2/First %d>>'%len(hdr),hdr+o5+o6))])
write('09_objstm_unsorted_header.pdf',d)

# 10: a %PDF header az elso 1024 byte-on tul (2000 byte szemet elotte), es a header sorban a verzio utan szoveg
#     ("%PDF-1.4 www.example.com", van ilyen iro). A header-sor feldolgozasanak korlatja a headerhez relativ kell
#     legyen, kulonben binheader=False es hamis "INVALID object type" hiba (README 10.11).
d,_,_=build(BASE,header_tail=b' www.example.com')
write('10_deep_header_text_after_version.pdf',b'J'*2000+d)
