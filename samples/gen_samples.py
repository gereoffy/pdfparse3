#! /usr/bin/python3

# Szintetikus teszt PDF-ek generalasa a samples/ konyvtarba (a generator melle).   futtatas: python3 samples/gen_samples.py
# Minden minta egy-egy (trukkos / serult) esetet tesztel, az elvart eredmenyek a test_samples.py-ban vannak.

import os
import zlib
import base64

OUT=os.path.dirname(os.path.abspath(__file__))   # (a mintak a generator mellett, a samples/ konyvtarban)

# PDF-kompatibilis LZW tomorites a dekoder ellenorzesehez. early: /EarlyChange (1: alap, a kodszelesseg egy koddal
# korabban no; 0: pontosan a szotar megtelesekor)
def lzw_compress(data,early=1):
    codes=[256]
    dic={bytes([i]):i for i in range(256)}
    nxt=258
    w=b''
    for c in data:
        wc=w+bytes([c])
        if wc in dic:
            w=wc
            continue
        codes.append(dic[w])
        if nxt<4094:
            dic[wc]=nxt
            nxt+=1
        else:
            codes.append(256)
            dic={bytes([i]):i for i in range(256)}
            nxt=258
        w=bytes([c])
    if w: codes.append(dic[w])
    codes.append(257)
    # bitek: a kodhossz a dekoder szotarmeretetol fugg (ami egy lepessel lemarad az enkoderetol)
    out=bytearray(); acc=0; nbits=0; dlen=258; bits=9; first=True
    for i,cd in enumerate(codes):
        acc=(acc<<bits)|cd; nbits+=bits
        while nbits>=8:
            nbits-=8; out.append((acc>>nbits)&0xFF)
        if cd==256:
            dlen=258; bits=9; first=True
        elif cd!=257:
            if not first: dlen+=1
            first=False
            if dlen+early>=(1<<bits) and bits<12: bits+=1
    if nbits: out.append((acc<<(8-nbits))&0xFF)
    return bytes(out)

def st(dic,data):
    return b'<<'+dic+b' /Length %d>>\nstream\n'%len(data)+data+b'\nendstream'

# klasszikus (ASCII xref tablas) pdf
def build(objs,trailer_extra=b'',header=b'%PDF-1.7\n%\xe2\xe3\xcf\xd3\n',xref_shift=None,tail=b'',startxref_bad=False,noend=()):
    out=bytearray(header); offs={}
    for oid,body in objs:
        offs[oid]=len(out)
        out+=b'%d 0 obj\n'%oid+body+(b'\nendobj\n' if oid not in noend else b'\n')
    xo=len(out); n=max(offs)+1
    out+=b'xref\n0 %d\n0000000000 65535 f \n'%n
    for i in range(1,n):
        if i in offs: out+=b'%010d 00000 n \n'%(offs[i]+(7 if xref_shift==i else 0))
        else: out+=b'0000000000 00000 f \n'
    out+=b'trailer\n<< /Size %d /Root 1 0 R %s>>\nstartxref\n%d\n%%%%EOF\n'%(n,trailer_extra,999999 if startxref_bad else xo)
    return bytes(out+tail)

# modern pdf: xref stream (W [1 4 2], PNG Up predictor)
def xrefstream(entries,size,prev=None,index=None):
    rows=[bytes([t])+f2.to_bytes(4,'big')+f3.to_bytes(2,'big') for o,t,f2,f3 in entries]
    out=bytearray(); prevrow=bytes(7)
    for r in rows:
        out+=bytes([2])+bytes((a-b)&0xff for a,b in zip(r,prevrow)); prevrow=r
    idx=b' /Index [%s]'%index if index else b''
    dic=b'/Type /XRef /Size %d /W [1 4 2]%s /Root 1 0 R /Filter /FlateDecode /DecodeParms << /Columns 7 /Predictor 12 >>'%(size,idx)
    if prev is not None: dic+=b' /Prev %d'%prev
    return st(dic,zlib.compress(bytes(out)))

PAYLOAD=b'MZ\x90\x00 this is an attachment '*50

def gen():
    P={}
    cat=(1,b'<< /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R >>')
    pages=(2,b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>')
    page=(3,b'<< /Type /Page /Parent 2 0 R >>')
    js=(4,b'<< /Type /Action /S /JavaScript /JS (app.alert\\(\\"hi\\"\\);\\n) >>')
    uri=(5,b'<< /Type /Action /S /URI /URI (http://evil.example/x) >>')
    fs=(6,b'<< /Type /Filespec /F (evil.exe) /EF << /F 7 0 R >> >>')
    ef=(7,st(b'/Type /EmbeddedFile /Filter /FlateDecode',zlib.compress(PAYLOAD)))
    P['basic']=build([cat,pages,page,js,uri,ef,fs])
    # JS es URI object streamben
    inner=b'<< /S /JavaScript /JS (this.exportDataObject\\(\\);) >> << /URI (https://hidden.example) >>'
    hdr=b'8 0 9 %d '%(inner.index(b'<< /URI'))
    P['objstm']=build([cat,pages,page,(8,st(b'/Type /ObjStm /N 2 /First %d /Filter /FlateDecode'%len(hdr),zlib.compress(hdr+inner)))])
    lz=lzw_compress(PAYLOAD)
    P['lzw']=build([cat,pages,page,(7,st(b'/Type /EmbeddedFile /Filter /LZWDecode',lz)),fs])
    P['lzw_trunc']=build([cat,pages,page,(7,st(b'/Type /EmbeddedFile /Filter /LZWDecode',lz[:len(lz)//2])),fs])
    # ASCIIHex whitespace-szel (a regi pdfparse2 vegtelen ciklusba esett)
    hx=PAYLOAD.hex().encode(); hx=b'\n'.join(hx[i:i+64] for i in range(0,len(hx),64))+b'>'
    P['ahex']=build([cat,pages,page,(7,st(b'/Type /EmbeddedFile /Filter /ASCIIHexDecode',hx)),fs])
    P['a85']=build([cat,pages,page,(7,st(b'/Type /EmbeddedFile /Filter [/ASCII85Decode /FlateDecode]',base64.a85encode(zlib.compress(PAYLOAD))+b'~>')),fs])
    # serult zlib content stream
    bad=bytearray(zlib.compress(b'BT /F1 12 Tf (hello) Tj ET'*100)); bad[20]^=0xff; bad[21]^=0xff
    P['badzlib']=build([cat,pages,page,(9,st(b'/Filter /FlateDecode',bytes(bad))),js])
    # csonka zlib csatolmany (a fele hianyzik)
    z=zlib.compress(PAYLOAD)
    P['truncfile']=build([cat,pages,page,(7,st(b'/Type /EmbeddedFile /Filter /FlateDecode',z[:len(z)//2])),fs])
    # teljes deflate adat, csak az adler32 checksum hianyzik a vegerol
    P['nochecksum']=build([cat,pages,page,(7,st(b'/Type /EmbeddedFile /Filter /FlateDecode',z[:-4])),fs])
    P['badxref']=build([cat,pages,page,js],xref_shift=2,tail=b'\x00'*5000)
    P['badstartxref']=build([cat,pages,page,js],startxref_bad=True)
    P['noeof']=build([cat,pages,page,js])[:-7]
    enc=(10,b'<< /Filter /Standard /V 2 /R 3 /Length 128 /O <00> /U <00> /P -4 >>')
    P['encrypted']=build([cat,pages,page,(7,st(b'/Type /EmbeddedFile /Filter /FlateDecode',b'\x12\x34garbage'*10)),fs,js,enc],trailer_extra=b'/Encrypt 10 0 R ')
    # szemet a header utan (a generalas utan beszurva, igy az xref offsetek is elcsusznak)
    g=build([cat,pages,page,js])
    P['garbage']=g.replace(b'%\xe2\xe3\xcf\xd3\n',b'%\xe2\xe3\xcf\xd3\nJUNKJUNK ',1)
    # szemet a header utan, es a JS obj ugyanabban a sorban kezdodik (a regi testpdf atugrotta)
    g=build([cat,pages,page,uri,ef,fs])
    P['garbage2']=g.replace(b'%\xe2\xe3\xcf\xd3\n',b'%\xe2\xe3\xcf\xd3\nJUNK 4 0 obj\n'+js[1]+b'\nendobj\n',1)
    # /J#53 = /JS, hexa string
    P['hexjs']=build([cat,pages,page,(4,b'<< /S /Java#53cript /J#53 <6170702e616c657274283129> >>')])
    # az elso obj a header soraban kezdodik, sorvege nelkul (a regi parserek atugrottak)
    g=build([js,cat,pages,page],header=b'%PDF-1.5 www.example.com')
    P['headerobj']=g
    g=build([js,cat,pages,page],header=b'%PDF-1.4\n%\xe1\xfc\xf6\xf3')
    P['headerobj2']=g
    # tobb obj-bol hianyzik az endobj (iro program hibaja): file-onkent egy hiba
    P['missing_endobj']=build([cat,pages,page,js,uri,ef,fs],noend=(1,2))
    # adatvesztes egy stream kozepen (1336 byte hianyzik), a file tobbi resze ep, de elcsuszott
    import random
    big=bytes(random.Random(7).getrandbits(8) for i in range(20000))
    g=build([cat,pages,page,(7,st(b'/Type /EmbeddedFile',big)),js,uri,fs])
    i=g.index(b'stream\n')+7+5000
    P['gap_in_stream']=g[:i]+g[i+1336:]
    # MS Outlook TNEF (winmail.dat): a pdf egy attAttachData attributum, elotte / utana a kontener tobbi adata
    P['tnef_container']=tnef(P['basic'],'evil.pdf')
    # Launch action: program inditasa parameterekkel
    P['launch']=build([(1,b'<< /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R >>'),pages,page,
                       (4,b'<< /S /Launch /Win << /F (cmd.exe) /P (/c calc.exe) >> >>')])
    # RTFD csomagba agyazott pdf (macOS): a header 16384 byte-nal, az elso 1024 byte-on tul
    # URI action-ok: javascript: URI (kozvetlen, hivatkozott string obj %XX kodolassal), ismetlodo http URI, es egy
    # Launch beagyazott Filespec-kel (a cel "/F javascript:void(0)", nem "/F /F ...")
    P['uri_js']=build([cat,pages,page,(5,b'<< /Type /Action /S /URI /URI (javascript:app.alert\\(1\\)) >>'),
                       (6,b'<< /Type /Action /S /URI /URI 7 0 R >>'),(7,b"(javascript:%20var%20x=window.open\\('/a'\\))"),
                       (8,b'<< /S /URI /URI (http://example.org/a) >>'),(9,b'<< /S /URI /URI (http://example.org/a) >>'),
                       (10,b'<< /S /Launch /F << /Type /Filespec /F (javascript:void\\(0\\)) >> >>')])
    P['rtfd_deep_header']=b'rtfd\x00\x00\x00\x00\x03\x00\x00\x00'+b'\x00'*(16384-12)+P['basic']
    P['notpdf']=b'hello world'*100
    P.update(gen_xrefstm())
    P.update(gen_encrypted())
    P.update(gen_text())
    P.update(gen_transfer())
    P.update(gen_crlf_nostream())
    P.update(gen_crlf_nostream(b'\n'))
    P.update(gen_structure(P['basic']))
    return P

# szabalyos MS-OXTNEF kontener (winmail.dat) egy csatolmannyal: alairas, kulcs, attributumok (szint, azonosito, hossz,
# adat, 16 bites checksum). Az uzenet torzse (attBody) akkora, hogy a pdf header -- mint a valodi winmail.dat-okban --
# az elso 1024 byte-on tul legyen. (Valodi TNEF parserrel -- pl. tnefparse -- kibonthato, a csatolmany byte-ra a pdf.)
def tnef(data,fname):
    import struct
    def attr(level,aid,v): return bytes([level])+struct.pack('<II',aid,len(v))+v+struct.pack('<H',sum(v)&0xFFFF)
    fn=fname.encode('ascii')+b'\x00'
    # attAttachment MAPI tulajdonsagok: PR_ATTACH_LONG_FILENAME (0x3707, PT_STRING8), 4 byte-ra kiegeszitve
    props=struct.pack('<III',1,0x3707001E,1)+struct.pack('<I',len(fn))+fn+b'\x00'*(-len(fn)%4)
    body=(b'Tisztelt Ugyfelunk! Mellekelten kuldjuk a dokumentumot.\r\n'*60)+b'\x00'
    return (struct.pack('<IH',0x223E9F78,0x0001)
            +attr(1,0x00089006,struct.pack('<I',0x00010000))                      # attTnefVersion
            +attr(1,0x00069007,struct.pack('<II',1252,0))                        # attOemCodepage
            +attr(1,0x00078008,b'IPM.Microsoft Mail.Note\x00')                    # attMessageClass
            +attr(1,0x00018004,b'Dokumentum\x00')                                 # attSubject
            +attr(1,0x0002800C,body)                                              # attBody
            +attr(2,0x00069002,struct.pack('<HIHHI',1,0xFFFFFFFF,0xFFFF,0xFFFF,0)) # attAttachRenddata (file)
            +attr(2,0x00018010,fn)                                                # attAttachTitle
            +attr(2,0x0006800F,data)                                              # attAttachData
            +attr(2,0x00069005,props))                                            # attAttachment

# valodi (Standard security handler) titkositas a pdfparse sajat RC4 / AES fuggvenyeivel: RC4 128 bit (V2 R3),
# AES-128 (V4 R4 AESV2), AES-256 (V5 R6 AESV3), ures felhasznaloi jelszoval ("owner" tulajdonosi jelszoval), es egy
# felhasznaloi jelszavas ("secret") RC4 valtozat, amit ures jelszoval nem lehet visszafejteni. Bennuk titkositott
# JS, URI, tomoritett content stream es csatolmany (a nevevel).
def gen_encrypted():
    import hashlib,struct,os,sys
    sys.path.insert(0,os.path.join(os.path.dirname(os.path.abspath(__file__)),'..'))
    from pdfparse3 import rc4,aes_cbc_encrypt,pdf_hash_2b,PDF_PAD
    md5=lambda x: hashlib.md5(x).digest()
    ID=bytes(range(0x30,0x40))
    P=-44
    pad=lambda pw: (pw+PDF_PAD)[:32]
    def make(kind,userpw=b'',res=b''):
        objs={}
        if kind=='aes256':
            fkey=bytes(range(0xA0,0xC0))
            vs,ks,ovs,oks=b'\x01'*8,b'\x02'*8,b'\x03'*8,b'\x04'*8
            U=pdf_hash_2b(userpw,vs,b'')+vs+ks
            UE=aes_cbc_encrypt(pdf_hash_2b(userpw,ks,b''),b'\0'*16,fkey)
            O=pdf_hash_2b(b'owner',ovs,U)+ovs+oks
            OE=aes_cbc_encrypt(pdf_hash_2b(b'owner',oks,U),b'\0'*16,fkey)
            perms=aes_cbc_encrypt(fkey,b'\0'*16,struct.pack('<iI',P,0xFFFFFFFF)+b'Tadb'+b'\0'*4)
            enc=(b'<< /Filter /Standard /V 5 /R 6 /Length 256 /CF << /StdCF << /CFM /AESV3 /AuthEvent /DocOpen /Length 32 >> >>'
                 b' /StmF /StdCF /StrF /StdCF /P %d /O <%s> /U <%s> /OE <%s> /UE <%s> /Perms <%s> >>'%(P,O.hex().encode(),U.hex().encode(),OE.hex().encode(),UE.hex().encode(),perms.hex().encode()))
            okey=lambda oid: fkey
            aes=True
        else:
            R=3 if kind=='rc4' else 4
            n=16
            h=md5(pad(b'owner'))
            for i in range(50): h=md5(h[:n])
            O=rc4(h[:n],pad(userpw))
            for i in range(1,20): O=rc4(bytes(b^i for b in h[:n]),O)
            k=md5(pad(userpw)+O+struct.pack('<I',P&0xFFFFFFFF)+ID)
            for i in range(50): k=md5(k[:n])
            fkey=k[:n]
            x=rc4(fkey,md5(PDF_PAD+ID))
            for i in range(1,20): x=rc4(bytes(b^i for b in fkey),x)
            U=x+b'\0'*16
            aes=kind=='aes'
            cf=b' /CF << /StdCF << /CFM /AESV2 /AuthEvent /DocOpen /Length 16 >> >> /StmF /StdCF /StrF /StdCF' if aes else b''
            enc=b'<< /Filter /Standard /V %d /R %d /Length 128%s /P %d /O <%s> /U <%s> >>'%(4 if aes else 2,R,cf,P,O.hex().encode(),U.hex().encode())
            okey=lambda oid: md5(fkey+struct.pack('<I',oid)[:3]+b'\0\0'+(b'sAlT' if aes else b''))[:16]
        def E(oid,data):
            k=okey(oid)
            if not aes: return rc4(k,data)
            iv=bytes((oid*7+i)&255 for i in range(16))
            p=16-len(data)%16
            return iv+aes_cbc_encrypt(k,iv,data+bytes([p])*p)
        H=lambda oid,data: b'<'+E(oid,data).hex().encode()+b'>'
        content=zlib.compress(b'BT /F1 12 Tf 72 712 Td (encrypted) Tj ET')
        return build([(1,b'<< /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R >>'),(2,b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>'),
                      (3,b'<< /Type /Page /Parent 2 0 R /Contents 6 0 R /Annots [9 0 R]'+res+b' >>'),
                      (4,b'<< /S /JavaScript /JS '+H(4,b'app.alert("encrypted js");')+b' >>'),
                      (5,b'<< /S /URI /URI '+H(5,b'http://enc.example/x')+b' >>'),
                      (6,st(b'/Filter /FlateDecode',E(6,content))),
                      (7,st(b'/Type /EmbeddedFile',E(7,b'MZ secret attachment'))),
                      (8,b'<< /Type /Filespec /F '+H(8,b'secret.exe')+b' /EF << /F 7 0 R >> >>'),
                      (9,b'<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] /A 5 0 R >>'),
                      (10,enc)],trailer_extra=b'/Encrypt 10 0 R /ID [<%s> <%s>] '%(ID.hex().encode(),ID.hex().encode()))
    return {'encrypted_rc4':make('rc4'),'encrypted_aes':make('aes'),'encrypted_aes256':make('aes256'),'encrypted_userpw':make('rc4',b'secret'),
            'text_encrypted':make('aes',res=b' /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >>')}

# szoveg kinyeres (pdf2str): fontok es kodolasok, TJ kerning / sorvegek, form XObject, inline kep, CID fontok.
# Az elvart szoveg a test_samples.py TEXT tablajaban.
def gen_text():
    P={}
    cat=(1,b'<< /Type /Catalog /Pages 2 0 R >>')
    pages=(2,b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>')
    def page(res,content,extra=()):
        return build([cat,pages,(3,b'<< /Type /Page /Parent 2 0 R /Resources '+res+b' /Contents 4 0 R >>'),
                      (4,st(b'/Filter /FlateDecode',zlib.compress(content)))]+list(extra))
    helv=b'<< /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >>'
    # Type0 Identity-H font /ToUnicode CMap-pel (bfchar + bfrange): ket sor
    txt='\u00c1rv\u00edzt\u0171r\u0151 t\u00fck\u00f6rf\u00far\u00f3g\u00e9p'
    chars=sorted(set(txt))
    code={c:i+1 for i,c in enumerate(chars)}
    cm=b'/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /T def 1 begincodespacerange <0000> <FFFF> endcodespacerange\n'
    cm+=b'%d beginbfchar\n'%(len(chars)-1)+b''.join(b'<%04X> <%04X>\n'%(code[c],ord(c)) for c in chars[1:])+b'endbfchar\n'
    cm+=b'1 beginbfrange\n<%04X> <%04X> <%04X>\nendbfrange\nendcmap CMapName currentdict /CMap defineresource pop end end\n'%(code[chars[0]],code[chars[0]],ord(chars[0]))
    hexs=lambda t: b'<'+b''.join(b'%04X'%code[c] for c in t)+b'>'
    w1,w2=txt.split(' ')
    P['text_tounicode']=page(b'<< /Font << /F1 5 0 R >> >>',b'BT /F1 12 Tf 72 700 Td '+hexs(w1)+b' Tj 0 -14 Td '+hexs(w2)+b' Tj ET',
        [(5,b'<< /Type /Font /Subtype /Type0 /BaseFont /T /Encoding /Identity-H /DescendantFonts [6 0 R] /ToUnicode 7 0 R >>'),
         (6,b'<< /Type /Font /Subtype /CIDFontType2 /BaseFont /T /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 600 >>'),
         (7,st(b'',cm))])
    # egybajtos WinAnsi font /Differences-szel: az o/u ketpontos ekezet glifnevvel (ohungarumlaut, udblacute)
    P['text_winansi_diff']=page(b'<< /Font << /F1 5 0 R >> >>',b'BT /F1 12 Tf 72 700 Td (Sz\xe9p \xf5sz \xfbr) Tj ET',
        [(5,b'<< /Type /Font /Subtype /TrueType /BaseFont /Arial /Encoding << /Type /Encoding /BaseEncoding /WinAnsiEncoding /Differences [245 /ohungarumlaut 251 /udblacute] >> >>')])
    # TJ: kis kerning nem szokoz, nagy (-400) szokoz; Td lefele uj sor; T* es ' operator; ugyanazon sorban tavoli Td szokoz
    P['text_tj_spacing']=page(helv,b'BT /F1 12 Tf 14 TL 72 700 Td [(Hel) -20 (lo) -400 (World)] TJ 0 -14 Td (second) Tj T* (third) Tj (fourth) \' 200 0 Td (far) Tj ET')
    # form XObject (sajat /Resources-szel), q/Q es cm
    P['text_form_xobject']=page(b'<< /XObject << /X1 5 0 R >> >>',b'q 1 0 0 1 50 50 cm /X1 Do Q',
        [(5,st(b'/Type /XObject /Subtype /Form /BBox [0 0 500 500] /Resources '+helv,b'BT /F1 10 Tf 0 0 Td (inside form) Tj ET'))])
    # inline kep a szoveg elott: a binaris adatban '(' es "EI" szovegresz is van
    P['text_inline_image']=page(helv,b'q 4 0 0 2 0 0 cm BI /W 4 /H 2 /BPC 8 /CS /G ID \x00(\xffEIx(\x10\x20 EI Q BT /F1 12 Tf 72 700 Td (after image) Tj ET')
    # /ToUnicode nelkuli Identity-H font, ahol a kod maga a Unicode (szokozzel): kinyerheto
    ident='Unicode identity text'
    P['text_identity_cid']=page(b'<< /Font << /F1 5 0 R >> >>',b'BT /F1 12 Tf 72 700 Td <'+b''.join(b'%04X'%ord(c) for c in ident)+b'> Tj ET',
        [(5,b'<< /Type /Font /Subtype /Type0 /BaseFont /U /Encoding /Identity-H /DescendantFonts [6 0 R] >>'),
         (6,b'<< /Type /Font /Subtype /CIDFontType2 /BaseFont /U /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> >>')])
    # /ToUnicode nelkuli Identity-H font glif-azonositokkal (nincs szokoz kod): ezek a karakterek kimaradnak, a masik
    # (Helvetica) font szovege megmarad
    P['text_gid_cid']=page(b'<< /Font << /F1 5 0 R /F2 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >>',
        b'BT /F1 12 Tf 72 700 Td <0003000400050006000700080009000A000B000C000D000E000F00100011001200130014001500160017> Tj ET BT /F2 12 Tf 72 600 Td (visible) Tj ET',
        [(5,b'<< /Type /Font /Subtype /Type0 /BaseFont /G /Encoding /Identity-H /DescendantFonts [6 0 R] >>'),
         (6,b'<< /Type /Font /Subtype /CIDFontType2 /BaseFont /G /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> >>')])
    # fedolap: az elso oldalon keves a szo, a kovetkezo oldalak hozzafuzodnek (minwords=100), amig a 100 szo meg nincs;
    # a 2-4. oldal egy koztes /Pages csomopont alatt, annak orokolt /Resources-aval. A 4. oldal mar nem kell.
    words=lambda w: ' '.join('%s%d'%(w,i) for i in range(60)).encode()
    P['text_multipage']=build([cat,(2,b'<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 4 >>'),
        (3,b'<< /Type /Page /Parent 2 0 R /Resources '+helv+b' /Contents 4 0 R >>'),
        (4,st(b'',b'BT /F1 24 Tf 72 700 Td (Cover page) Tj ET')),
        (5,b'<< /Type /Pages /Parent 2 0 R /Kids [6 0 R 8 0 R 10 0 R] /Count 3 /Resources '+helv+b' >>'),
        (6,b'<< /Type /Page /Parent 5 0 R /Contents 7 0 R >>'),
        (7,st(b'',b'BT /F1 8 Tf 72 700 Td ('+words('two')+b') Tj ET')),
        (8,b'<< /Type /Page /Parent 5 0 R /Contents 9 0 R >>'),
        (9,st(b'',b'BT /F1 8 Tf 72 700 Td ('+words('three')+b') Tj ET')),
        (10,b'<< /Type /Page /Parent 5 0 R /Contents 11 0 R >>'),
        (11,st(b'',b'BT /F1 12 Tf 72 700 Td (never shown) Tj ET'))])
    return P

# szerkezeti hibak: szemet a header elott, csonka file, iro program altal elrontott xref
def gen_structure(basic):
    import re
    P={}
    P['junk_bom']=b'\xef\xbb\xbf'+basic
    P['junk_html']=b'<!-- tracking script -->\r\n<script>var x=1;</script>\r\n'+basic
    # html a %%EOF utan (a "trailer" szo nem jelenthet csonka file-t)
    P['junk_html_tail']=basic+b'<!DOCTYPE html>\n<html><body>Watch the trailer xref page 1 0 of 2</body></html>\n'
    P['truncated_zero']=basic[:len(basic)//2]+b'\x00'*4096
    # linearizalt file, amibol csak az elso oldal szekcioja maradt meg (startxref 0), a vege hianyzik
    head=(b'%PDF-1.7\n%\xe2\xe3\xcf\xd3\n9 0 obj\n<< /Linearized 1 /L 99999 >>\nendobj\n'
          b'xref\n9 1\n0000000015 00000 n \ntrailer\n<< /Size 10 /Root 1 0 R /Prev 99990 >>\nstartxref\n0\n%%EOF\n')
    i=basic.index(b'1 0 obj'); j=basic.index(b'\nxref\n')
    P['truncated_lin']=head+basic[i:j][:-40]
    # minden xref bejegyzes = 1 (iro program hibaja)
    P['bogus_xref']=re.sub(rb'\d{10} 00000 n',b'0000000001 00000 n',basic)
    # az xref bejegyzesek egy obj-mal el vannak csuszva: az i. obj helyen az i+1. offsetje all
    offs=re.findall(rb'(\d{10}) 00000 n',basic)
    rot=offs[1:]+offs[:1]
    it=iter(rot)
    P['misnumbered']=re.sub(rb'\d{10} 00000 n',lambda m:next(it)+b' 00000 n',basic)
    # macOS Quartz: a hasznalaton kivuli obj szamok "0000000000 00000 n" bejegyzessel (nem hiba)
    q=build([(1,b'<< /Type /Catalog /Pages 2 0 R >>'),(2,b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>'),
             (3,b'<< /Type /Page /Parent 2 0 R >>'),(7,b'<< /Producer (macOS Version 13.4 Quartz PDFContext) >>')])
    P['xref_zero_unused']=q.replace(b'0000000000 00000 f \n',b'0000000000 00000 n \n').replace(b'0000000000 65535 n \n',b'0000000000 65535 f \n')
    # egy letezo obj bejegyzese 0 (az offset tenyleg elveszett): hiba
    P['xref_zero_existing']=basic.replace(offs[1]+b' 00000 n',b'0000000000 00000 n',1)
    # whitespace a header elott (artalmatlan), MacBinary fejlec (regi Mac)
    P['junk_ws']=b'\n '+basic
    mb=bytearray(128); name=b'evil.pdf'; mb[1]=len(name); mb[2:2+len(name)]=name; mb[65:73]=b'PDF CARO'; mb[83:87]=len(basic).to_bytes(4,'big')
    P['junk_macbinary']=bytes(mb)+basic+b'\x00'*(-len(basic)%128)
    # alairt pdf: a /Contents helyfoglalo szazezer nulla (a regex nem lehet negyzetes ideju)
    sig=(8,b'<< /Type /Sig /Filter /Adobe.PPKLite /Contents <'+b'0'*200000+b'> /ByteRange [0 0 0 0] >>')
    P['signed_placeholder']=build([(1,b'<< /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R >>'),(2,b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>'),
                                   (3,b'<< /Type /Page /Parent 2 0 R >>'),(4,b'<< /S /JavaScript /JS (app.alert\\(\\"hi\\"\\);\\n) >>'),sig])
    # xref tabla utan (startxref nelkul) meg obj-ek jonnek: a linearis olvasas ne nyelje le oket
    i=basic.index(b'\nxref\n')+1; j=basic.index(b'startxref')
    js2=b'9 0 obj\n<< /S /JavaScript /JS (after_xref\\(\\)) >>\nendobj\n'
    P['xref_then_objs']=basic[:j]+js2+basic[j:]
    # Oracle PDF driver: le nem zart string az Info-ban; a linearis olvasas ne nyelje le miatta a file tobbi reszet
    P['unterminated_string']=build([(9,b'<<\r\n/Creator (Developer 2000)\r\n/CreatorDate (\r\n/Author (Oracle Reports)\r\n>>'),
        (1,b'<< /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R >>'),(2,b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>'),
        (3,b'<< /Type /Page /Parent 2 0 R >>'),(4,b'<< /S /JavaScript /JS (after_string\\(\\)) >>')],trailer_extra=b'/Info 9 0 R ')
    # teljes dokumentum, utana egy regi file maradeka (obj kozeperol indul), a meret blokkmeretre kerekitve: nem csonka
    lo=basic+b'\n/Subtype/Type1\n/BaseFont/Arial\n>>\nendobj\n12 0 obj\n<< /Type /Font /Widths [500 500'
    P['leftover_after_eof']=lo+b' 500'*((-len(lo)%512)//4)+b' 5'[:(-len(lo)%512)%4]
    # teljes dokumentum, utana egy felbeszakadt incremental update (obj hataron kezdodik): csonka
    P['truncated_update']=basic+b'8 0 obj\n<< /Type /Annot /Subtype /Link /A << /S /URI /URI (http://x.example) >> >>\nendobj\n9 0 obj\n<< /Length 500 >>\nstream\nabc'
    return P

# ep, CRLF sorvegu pdf stream nelkul, majd a CR-eket kiszedik: csak az xref eltolasabol ismerheto fel
def gen_crlf_nostream(nl=b'\r\n'):
    out=bytearray(b'%PDF-1.3\r\n%\xe2\xe3\xcf\xd3\r\n'.replace(b'\r\n',nl)); offs={}
    objs=[(1,b'<< /Type /Catalog /Pages 2 0 R /Outlines 3 0 R >>'),(2,b'<< /Type /Pages /Kids [4 0 R] /Count 1 >>'),
          (3,b'<< /Type /Outlines\r\n/Count 0\r\n>>'),(4,b'<< /Type /Page\r\n/Parent 2 0 R\r\n/MediaBox [0 0 612 792]\r\n>>'),
          (5,b'<< /Producer (test)\r\n/Title (x)\r\n>>')]
    for oid,body in objs:
        offs[oid]=len(out); out+=(b'%d 0 obj\r\n'%oid+body+b'\r\nendobj\r\n').replace(b'\r\n',nl)
    xo=len(out)
    e=b' \n' if nl==b'\n' else nl   # az xref sorok 20 byte-osak: LF-es file-nal "szokoz LF" a vege
    out+=b'xref'+nl+b'0 6'+nl+b'0000000000 65535 f'+e+b''.join(b'%010d 00000 n'%offs[i]+e for i in range(1,6))
    out+=(b'trailer\r\n<< /Size 6 /Root 1 0 R /Info 5 0 R >>\r\nstartxref\r\n%d\r\n%%%%EOF\r\n'%xo).replace(b'\r\n',nl)
    if nl==b'\n':
        # csak szoveg (binaris komment 0x80 felett, de 32 alatt csak LF): az LF -> CRLF serules byte-ra pontosan visszaalakithato
        return {'lf_text_ok':bytes(out),'lf_text_crlf':bytes(out).replace(b'\n',b'\r\n')}
    return {'crlf_nostream_ok':bytes(out),'crlf_nostream':bytes(out).replace(b'\r\n',b'\n')}

# sorvege-serules szoveges atvitelnel (base64 nelkul kuldott email, ASCII FTP)
def gen_transfer():
    import random,re
    rnd=random.Random(42)
    P={}
    cat=(1,b'<< /Type /Catalog /Pages 2 0 R >>')
    pages=(2,b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>')
    page=(3,b'<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>')
    content=(4,st(b'/Filter /FlateDecode',zlib.compress(b'BT /F1 12 Tf 72 712 Td (hello) Tj ET\n'*50)))
    binary=bytes(rnd.getrandbits(8) for i in range(30000))   # veletlen binaris adat, van benne 0A es 0D 0A is
    def files(txt):
        return [(5,st(b'/Type /EmbeddedFile /Filter /FlateDecode',zlib.compress(binary))),
                (6,st(b'/Type /EmbeddedFile',txt)),
                (7,st(b'/Type /EmbeddedFile /Filter /FlateDecode',zlib.compress(PAYLOAD+binary[:5000])))]
    lf=build([cat,pages,page,content]+files(b'echo hello\nexit 0\n'*40))
    crlf=build([cat,pages,page,content]+files(b'echo hello\r\nexit 0\r\n'*40))  # CRLF-es szoveges csatolmany
    P['transfer_ok']=crlf                                    # ep file, nem szabad jeleznie
    P['transfer_crlf']=re.sub(rb'(?<!\r)\n',b'\r\n',lf)      # "okos" konverter: csak a maganyos LF-ekbol lesz CRLF
    P['transfer_crlf_naive']=lf.replace(b'\n',b'\r\n')       # naiv konverter: minden LF ele CR
    P['transfer_lf']=crlf.replace(b'\r\n',b'\n')             # a CR-eket kiszedik (CRLF -> LF)
    # regebbi pdf: a header utan nincs binaris komment sor, a levelezo szovegnek nezi -> tipikus email-serules
    lf2=build([cat,pages,page,content]+files(b'echo hello\nexit 0\n'*40),header=b'%PDF-1.3\n')
    P['transfer_crlf_textheader']=re.sub(rb'(?<!\r)\n',b'\r\n',lf2)
    return P

def gen_xrefstm():
    P={}
    out=bytearray(b'%PDF-1.7\n%\xe2\xe3\xcf\xd3\n'); off={}
    def add(oid,body):
        off[oid]=len(out); out.extend(b'%d 0 obj\n'%oid+body+b'\nendobj\n')
    # objstm #10: 1 catalog, 2 pages, 3 page (a /Type nem elol), 4 JS action (ref + beagyazott), 5 #xx nevek + oktalis JS
    inner=[(1,b'<< /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R /Names << /EmbeddedFiles << /Names [(a) 6 0 R] >> >> >>'),
           (2,b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>'),
           (3,b'<< /Resources << /Font << /F1 << /Type /Font >> >> >> /Type /Page /Parent 2 0 R /AA << /O 5 0 R >> >>'),
           (4,b'<< /S /JavaScript /JS 9 0 R /Next << /S /JavaScript /JS (second\\(\\)) >> >>'),
           (5,b'<< /S /Java#53cript /J#53 (\\145\\166\\141\\154\\(1\\)) >>')]
    hdr=b''; body=b''
    for o,b in inner: hdr+=b'%d %d '%(o,len(body)); body+=b+b' '
    add(10,st(b'/Type /ObjStm /N %d /First %d /Filter /FlateDecode'%(len(inner),len(hdr)),zlib.compress(hdr+body)))
    # /EF elol, /UF utf-16 nev
    add(6,b'<< /EF << /F 7 0 R >> /Type /Filespec /F (old.exe) /UF <feff006500760069006c002e006500780065> >>')
    # a csatolmany adataban 'endstream' szoveg van
    add(7,st(b'/Type /EmbeddedFile',b'MZ attachment with endstream inside\nendstream\n more data'*20))
    add(9,st(b'/Filter /FlateDecode',zlib.compress(b'app.alert("stream js");')))
    # rejtett obj, nincs benne az xref-ben
    out.extend(b'99 0 obj\n<< /S /JavaScript /JS (hidden\\(\\)) >>\nendobj\n')
    xo=len(out)
    ents=sorted([(0,0,0,65535)]+[(o,2,10,i) for i,(o,b) in enumerate(inner)]+
                [(6,1,off[6],0),(7,1,off[7],0),(8,1,xo,0),(9,1,off[9],0),(10,1,off[10],0)])
    add(8,xrefstream(ents,11))
    out.extend(b'startxref\n%d\n%%%%EOF\n'%xo)
    P['xrefstm']=bytes(out)
    # serult xref stream adat
    d=bytearray(out); i=d.rfind(b'>>\nstream\n')+10; d[i+20]^=0x55
    P['xrefstm_corrupt']=bytes(d)
    # incremental update: obj 9 uj verzioja, uj xref stream /Prev-vel
    add(9,st(b'/Filter /FlateDecode',zlib.compress(b'app.alert("NEW js");')))
    xo2=len(out)
    add(12,xrefstream([(9,1,off[9],0),(12,1,xo2,0)],13,prev=xo,index=b'9 1 12 1'))
    out.extend(b'startxref\n%d\n%%%%EOF\n'%xo2)
    P['incr']=bytes(out)
    # a /Prev 3 byte-tal elcsuszik
    bad=bytearray(out); i=bad.rfind(b'/Prev %d'%xo); bad[i+6:i+6+len(str(xo))]=str(xo+3).encode()
    P['incr_badprev']=bytes(bad)
    return P

if __name__=='__main__':
    os.makedirs(OUT,exist_ok=True)
    P=gen()
    for k,v in P.items():
        with open(os.path.join(OUT,k+'.pdf'),'wb') as f: f.write(v)
    print("%d samples -> %s"%(len(P),OUT))
