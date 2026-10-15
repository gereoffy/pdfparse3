#! /usr/bin/python3

# A lexer es a dekoderek unit tesztjei.   futtatas: python3 samples/test_unit.py   (vagy pytest)

import os
import sys
import zlib

sys.path.insert(0,os.path.join(os.path.dirname(os.path.abspath(__file__)),".."))
import pdfparse3 as P
import gen_samples

def tok(b): return P.parse_pdf_param(b,0,len(b))[1]
def s(b): return tok(b).get()

def test_string_escapes():
    assert s(rb'(app.alert\(\"hi\"\);\n)')==b'app.alert("hi");\n'
    assert s(b'(eva\\\nl)')==b'eval'            # sorfolytatas
    assert s(b'(eva\\\r\nl)')==b'eval'
    assert s(rb'(\141\160\160)')==b'app'         # oktalis
    assert s(rb'(\1a\12b\0011)')==b'\x01a\nb\x011'
    assert s(rb'(\777)')==b'\xff'
    assert s(rb'(a\\b\qc)')==b'a\\bqc'           # ismeretlen escape: a backslash eldobva
    assert s(b'(a(b)c)')==b'a(b)c'               # kiegyensulyozott zarojelek
    assert s(b'(a\r\nb\rc)')==b'a\nb\nc'         # sorvege -> LF

def test_hex_string():
    assert s(b'<6 1 7 0\n7>')==b'ap\x70'         # whitespace a nibble-ok kozott is
    assert s(b'<617>')==b'ap'                    # hianyzo utolso nibble

def test_names():
    assert tok(b'/J#53')==b'/JS'
    assert tok(b'/Java#53cript')==b'/JavaScript'
    assert tok(b'/A#2')==b'/A#2'                 # hibas #xx: marad
    assert tok(b'/JS')==b'/JS'                   # rovid nev a buffer vegen

def test_ascii85():
    import base64
    for data in [b'',b'a',b'hello',os.urandom(1000),b'\0'*12+b'x']:
        assert P.ASCII85Decode(b'<~'+base64.a85encode(data)+b'~>')==(bytearray(data),None)
    assert P.ASCII85Decode(b'87cURD]i,"Ebo80~>')==(bytearray(b'Hello World!'),None)
    d,n=P.ASCII85Decode(b'uuuuu~>')                       # 32 biten tulcsordulo csoport: levagva, megjegyzes
    assert len(d)==4 and 'overflow' in n
    d,n=P.ASCII85Decode(b'y87cURD]i,"Ebo80~>')            # btoa 'y' = 4 szokoz
    assert d==b'    Hello World!' and 'y' in n

def test_asciihex_runlength():
    assert P.ASCIIHexDecode(b'61 6\n2 63>')==(bytearray(b'abc'),None)
    d,e=P.ASCIIHexDecode(b'61x62>')
    assert d==b'ab' and e
    assert P.RunLengthDecode(bytes([2])+b'abc'+bytes([254])+b'x'+bytes([128]))==b'abcxxx'

def test_inflate():
    data=b'x'*100000+bytes(range(256))*100
    z=zlib.compress(data)
    assert P.inflate(z)==(data,None,None)
    dd,e,n=P.inflate(z[:-10])                    # csonka: hiba, de a nagy resze megvan
    assert e and len(dd)>90000
    dd,e,n=P.inflate(b'')                        # ures stream nem hiba
    assert dd==b'' and e==None
    for k in [1,2,4]:                            # hianyzo (vagy reszleges) checksum: nem hiba, csak megjegyzes
        dd,e,n=P.inflate(z[:-k])
        assert dd==data and e==None and n
    bad=bytearray(z); bad[-3]^=0x55              # rossz checksum (a felso 16 bit is): minden adat megvan, de hiba
    dd,e,n=P.inflate(bytes(bad))
    assert e and dd==data and 'mismatch' in e
    bad=bytearray(z); bad[-1]^=0x55              # csak az also 16 bit rossz: az iro program checksum hibaja
    dd,e,n=P.inflate(bytes(bad))
    assert e and dd==data and 'bug of the writer' in e
    c=zlib.compressobj(); zs=c.compress(data)+c.flush(zlib.Z_SYNC_FLUSH)
    dd,e,n=P.inflate(zs)                         # sync flush, nincs utolso blokk: teljes adat, megjegyzes
    assert dd==data and e==None and 'sync flush' in n
    r=zlib.compressobj(9,zlib.DEFLATED,-15); rz=r.compress(data)+r.flush()
    dd,e,n=P.inflate(rz)                         # zlib header nelkuli raw deflate
    assert dd==data and e==None and 'raw deflate' in n
    for ws in [b'\n',b'\r\n',b' \r\n']:           # ures stream, csak sorvege
        assert P.inflate(ws)==(b'',None,None)
    bad=bytearray(z); bad[len(z)//2]^=0xff         # serult adat a kozepen: hiba, de a hiba elotti resz megvan
    dd,e,n=P.inflate(bytes(bad))
    assert e and len(dd)>0

def test_lzw():
    for data in [gen_samples.PAYLOAD,os.urandom(20000),b'a'*50000,bytes(range(256))*300]:
        lz=P.LZWDecode(gen_samples.lzw_compress(data))
        assert lz.decode()==data and lz.error==None
    lz=P.LZWDecode(gen_samples.lzw_compress(b'abc'*1000)[:50])
    lz.decode()
    assert lz.noeod and not lz.error                 # csonka LZW: nincs EOD (a PDFStream donti el: pontos /Length -> megjegyzes)
    s=P.PDFStream(gen_samples.lzw_compress(b'abc'*1000)[:50],[b'/LZWDecode'])
    s.decode()
    assert s.error                                   # bizonytalan hossznal (nem exact) a hianyzo EOD hiba
    s.exact=True
    s.decode()
    assert s.error==None and 'no EOD' in s.note
    for data in [bytes(range(256))*20,os.urandom(5000)]:  # /EarlyChange 0: az 511. kod utan mas a kodszelesseg
        lz=P.LZWDecode(gen_samples.lzw_compress(data,early=0),0)
        assert lz.decode()==data and lz.error==None
        assert P.LZWDecode(gen_samples.lzw_compress(data,early=0),1).decode()!=data   # rossz EarlyChange-dzsel elromlik 

def test_crypt():
    # FIPS-197 C.1 / C.3 (egy blokk, IV=0), CBC oda-vissza, RC4 tesztvektor, es az R6 hash
    pt=bytes.fromhex('00112233445566778899aabbccddeeff')
    for key,ct in [(bytes(range(16)),'69c4e0d86a7b0430d8cdb78070b4c55a'),(bytes(range(32)),'8ea2b7ca516745bfeafc49904b496089')]:
        assert P.aes_cbc_encrypt(key,b'\0'*16,pt).hex()==ct
        assert P.aes_cbc_decrypt(key,b'\0'*16,bytes.fromhex(ct))==pt
    key=os.urandom(16); iv=os.urandom(16); data=os.urandom(16*50)
    assert P.aes_cbc_decrypt(key,iv,P.aes_cbc_encrypt(key,iv,data))==data
    assert P.rc4(b'Key',b'Plaintext').hex()=='bbf316e8d940af0ad3'
    assert len(P.pdf_hash_2b(b'',b'12345678',b''))==32

def test_objs_content():
    # objs_tree: teljes szerkezet (beagyazott dict / tomb, hivatkozas), num: valos szam bytes-kent is
    objs=['<',b'/A',1,b'/B','[',2,0,b'R','<',b'/C',b'14.5','>',']',b'/D','>']
    v,i=P.objs_tree(objs,0)
    assert i==len(objs) and v=={b'/A':1,b'/B':[('R',2),{b'/C':b'14.5'}],b'/D':None}
    assert P.num(b'14.5')==14.5 and P.num(b'-.5')==-0.5 and P.num(7)==7 and P.num(b'/X',None) is None
    # content_ops: operandusok, TJ tomb, inline kep egyben (a binaris adat '(' es "EIx" resze nem zavar)
    ops=list(P.content_ops(b"BT /F1 12 Tf [(A) -120 (B)] TJ ET BI /W 2 /H 1 ID \x00(EIx\xff EI (after) Tj"))
    assert [op for op,a in ops]==[b'BT',b'Tf',b'TJ',b'ET',b'BI',b'Tj']
    assert ops[1][1]==[b'/F1',12] and ops[2][1][0][1]==-120 and ops[2][1][0][0].get()==b'A'
    assert ops[4][1][0]=={b'/W':2,b'/H':1} and ops[4][1][1]==b'\x00(EIx\xff' and ops[5][1][0].get()==b'after'

def test_get_obj():
    # get_obj / deref: kozvetlen obj es object stream tagja; hivatkozas lanc
    import contextlib,io
    S=gen_samples.gen()
    pdf=P.PDFParser(S['xrefstm'])
    with contextlib.redirect_stdout(io.StringIO()): pdf.parse()
    root=pdf.deref(('R',pdf.root))
    assert type(root)==dict and root.get(b'/Type')==b'/Catalog'       # (objstm-ben)
    pages=pdf.deref(root[b'/Pages'])
    assert pages[b'/Count']==1 and type(pdf.deref(pages[b'/Kids'][0]))==dict
    assert pdf.get_obj(999999)==(None,None)

def test_text_robust():
    # pdf2str: hibas operandus (lista / dict a Tf, Do elott) ne dobjon kivetelt; a tulcsordulo bfrange ne vigye el a CMap-et
    import contextlib,io,pdf2str
    def page(content):
        return gen_samples.build([(1,b'<< /Type /Catalog /Pages 2 0 R >>'),(2,b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>'),
            (3,b'<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> /Contents 4 0 R >>'),
            (4,gen_samples.st(b'',content))])
    for content,want in [(b'BT [1 2] 12 Tf (x) Tj ET',None),(b'BT <</A 1>> 12 Tf (x) Tj ET',None),(b'[/X1] Do BT /F1 12 Tf (ok) Tj ET','ok')]:
        with contextlib.redirect_stdout(io.StringIO()):
            assert pdf2str.parse_pdf_text(page(content))[3]==want, content
    m,lens=pdf2str.parse_cmap(b'1 begincodespacerange <00> <FF> endcodespacerange 1 beginbfchar <41> <0041> endbfchar '
                              b'1 beginbfrange <f0> <ff> <fa> endbfrange 1 beginbfrange <61> <62> <0061> endbfrange')
    assert m[0x41]=='A' and m[0x61]=='a' and m[0x62]=='b' and 0xF5 in m and 0xF6 not in m

def test_regex_speed():
    # alairt pdf: a /Contents <0000...> helyfoglalo szazezer jegyu szamsor, ez nem lehet lassu
    import time
    d=b'%PDF-1.7\n1 0 obj\n<< /Contents <'+b'0'*200000+b'> >>\nendobj\n2 0 obj\n<< >>\nendobj\n'
    t=time.time()
    starts=[m.start() for m in P.re_objstart.finditer(d)]
    assert len(starts)==2 and P.re_structure.search(d,20) and time.time()-t<2

def test_unterminated_strings():
    # le nem zart string: az elso sorvegnel zarul; sorvege nelkul a vegeig tart
    def u(b):
        p,x=P.parse_pdf_param(b,0,len(b),lambda m:None)
        return p,x.get()
    assert u(b'(abc\ndef')==(4,b'abc')
    assert u(b'(a\\)b')==(5,b'a)b')            # (\) nem zar, sorvege nincs: a vegeig)
    assert u(b'(a(b)c')==(6,b'a(b)c')
    assert u(b'(a\\')==(3,b'a\\')               # (a vegen allo backslash)
    assert u(b'(a\\\\)b')==(5,b'a\\')           # (\\ utan a ) mar zar)
    # binaris szemet sok '(' bajttal (pl. a %%EOF utan): nem lehet negyzetes ideju
    import time
    # (a masodik: "\(" parok, amiket a korabbi bejaras escape-kent atugrott, igy nincsenek a cache-ben: a cache-t a
    # bejart tartomany barmely '('-jara hasznalni kell, kulonben soronkent ujraszkennel)
    for d in (b'\n'.join(b'\x02\x8a\x00(\xa0'*40 for i in range(1500)),b'\r\n'.join(b'\x02\\(\xa0'*40 for i in range(1500))):
        t=time.time()
        p,n=0,0
        while p<len(d):
            p,x=P.parse_pdf_param(d,p,len(d),lambda m:None)
            n+=1
        assert n>1000 and time.time()-t<2

def test_predictor():
    raw=bytes([2,1,2,3, 2,1,1,1])                # PNG Up
    assert P.unpredict(raw,{b'/Predictor':12,b'/Columns':3})==bytes([1,2,3,2,3,4])
    raw=bytes([1,1,1,1, 0,5,6,7])                # PNG Sub, None
    assert P.unpredict(raw,{b'/Predictor':12,b'/Columns':3})==bytes([1,2,3,5,6,7])

def test_dict():
    o=[1,0,b'obj','<',b'/Type',b'/XRef',b'/W','[',1,2,1,']',b'/Length',5,0,b'R',b'/DecodeParms','<',b'/Columns',4,b'/Predictor',12,'>','>']
    t=P.top_dict(o)
    assert t[b'/Type']==b'/XRef' and t[b'/W']==None and t[b'/Length']==('R',5)
    assert P.objs_decodeparms(o)=={b'/Columns':4,b'/Predictor':12}


if __name__=='__main__':
    n=0
    for name,f in list(globals().items()):
        if name.startswith('test_') and callable(f):
            f()
            n+=1
    print("%d unit tests OK"%(n))
