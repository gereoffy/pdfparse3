#! /usr/bin/python3

# A gen_samples.py mintainak ellenorzese: hibaszam (content es validate modban) es a kinyert tartalom.
# futtatas: python3 samples/test_samples.py [-v]   (vagy pytest)

import os
import sys
import io
import contextlib
import re

sys.path.insert(0,os.path.join(os.path.dirname(os.path.abspath(__file__)),".."))
import pdfparse3 as pdfparse
import gen_samples

PAYLOAD=gen_samples.PAYLOAD
JS=b'app.alert("hi");\n'
EVIL=('evil.exe',PAYLOAD)
EVIL2=('evil.exe',b'MZ attachment with endstream inside\nendstream\n more data'*20)
XJS=[('pdfstream.js',b'second()'),('pdfstream.js',b'eval(1)')]

# minta: (hibaszam, hibaszam validate modban, content [(nev,adat)], csak content modban (validate-ben nem) latszo elemek)
# adat=None: csak a nevet ellenorizzuk
EXPECTED={
    'basic':        (0, 0, [('pdfstream.js',JS),EVIL], []),
    'objstm':       (0, 0, [('pdfstream.js',b'this.exportDataObject();')], []),
    'lzw':          (0, 0, [EVIL], []),
    'lzw_trunc':    (0, 0, [('evil.exe',None)], []),     # pontos /Length mellett a hianyzo LZW EOD csak megjegyzes (pdfparse3 10.9)
    'ahex':         (0, 0, [EVIL], []),
    'a85':          (0, 0, [EVIL], []),
    'badzlib':      (11,11, [('pdfstream.js',JS)], []),
    'truncfile':    (1, 1, [('evil.exe',None)], []),
    'nochecksum':   (0, 0, [EVIL], []),     # hianyzo adler32: nem hiba (valid pdf-ekben is elofordul)
    'badxref':      (1, 1, [('pdfstream.js',JS)], []),     # rossz offset (a nulla padding az EOF utan nem hiba)
    'badstartxref': (1, 1, [('pdfstream.js',JS)], []),
    'noeof':        (5, 5, [('pdfstream.js',JS)], []),
    'encrypted':    (0, 0, [('pdfstream.dat',b'\x12\x34garbage'*10),('pdfstream.js',JS)], []),
    'garbage':      (2, 2, [('pdfstream.js',JS)], []),     # rossz startxref + az elcsuszott xref bejegyzesek osszesitve
    'garbage2':     (4, 4, [('pdfstream.js',JS),EVIL], []),
    'hexjs':        (0, 0, [('pdfstream.js',b'app.alert(1)')], []),
    'notpdf':       (99,99,None, []),
    'xrefstm':      (0, 0, XJS+[EVIL2,('pdfstream.js',b'app.alert("stream js");')], [('pdfstream.js',b'hidden()')]),
    'xrefstm_corrupt':(13,13,XJS+[EVIL2,('pdfstream.js',b'app.alert("stream js");'),('pdfstream.js',b'hidden()')], []),  # xref nelkul validate is linearis
    'incr':         (0, 0, XJS+[EVIL2,('pdfstream.js',b'app.alert("NEW js");')], [('pdfstream.js',b'hidden()')]),
    'incr_badprev': (1, 1, XJS+[EVIL2,('pdfstream.js',b'app.alert("NEW js");')], [('pdfstream.js',b'hidden()')]),
    # sorvege-serules: a csatolmanyok kijonnek (serulten), a TRANSFER diagnozis +10
    'transfer_ok':         (0, 0, [('pdfstream.dat',None)]*3, []),
    'transfer_crlf':       (19,19,[('pdfstream.dat',None)]*3, []),
    'transfer_crlf_naive': (19,19,[('pdfstream.dat',None)]*3, []),
    'transfer_crlf_textheader':(19,19,[('pdfstream.dat',None)]*3, []),
    'transfer_lf':         (14,14,[('pdfstream.dat',None)]*3, []),
    'crlf_nostream_ok':    (0, 0, [], []),
    'crlf_nostream':       (5, 5, [], []),                  # csak az xref eltolasabol ismerheto fel; szoveges: visszaalakitva (figyelmeztetes)
    'lf_text_ok':          (0, 0, [], []),
    'lf_text_crlf':        (5, 5, [], []),                  # szoveges LF -> CRLF: visszaalakitva, csak a TRANSFER marad (figyelmeztetes)
    # az elso obj (JS) a header soraban kezdodik
    'headerobj':           (0, 0, [('pdfstream.js',JS)], []),
    'headerobj2':          (0, 0, [('pdfstream.js',JS)], []),
    # szerkezeti hibak
    # valodi titkositas ures felhasznaloi jelszoval: visszafejtve (a csatolmany a nevevel); a jelszavas nem
    'encrypted_rc4':       (0, 0, [('pdfstream.js',b'app.alert("encrypted js");'),('secret.exe',b'MZ secret attachment')], []),
    'encrypted_aes':       (0, 0, [('pdfstream.js',b'app.alert("encrypted js");'),('secret.exe',b'MZ secret attachment')], []),
    'encrypted_aes256':    (0, 0, [('pdfstream.js',b'app.alert("encrypted js");'),('secret.exe',b'MZ secret attachment')], []),
    'encrypted_userpw':    (0, 0, [('pdfstream.js',None),('pdfstream.dat',None)], []),
    # szoveg kinyeres (pdf2str) mintai: a parser szamara hibatlanok; a szoveg a TEXT tablaban
    'text_tounicode':      (0, 0, [], []),
    'text_winansi_diff':   (0, 0, [], []),
    'text_tj_spacing':     (0, 0, [], []),
    'text_form_xobject':   (0, 0, [], []),
    'text_inline_image':   (0, 0, [], []),
    'text_identity_cid':   (0, 0, [], []),
    'text_gid_cid':        (0, 0, [], []),
    'text_encrypted':      (0, 0, [('pdfstream.js',b'app.alert("encrypted js");'),('secret.exe',b'MZ secret attachment')], []),
    'uri_js':              (0, 0, [('pdfstream.launch',b'/F javascript:void(0)')],
                            [('pdfstream.uri.js',b'app.alert(1)'),('pdfstream.uri.js',b"var x=window.open('/a')")]),   # (+ pdfstream.urls, l. test_urls)
    'junk_bom':            (5, 5,  [('pdfstream.js',JS),EVIL], []),
    'junk_html':           (5, 5, [('pdfstream.js',JS),EVIL,('pdfstream.html',None),('pdfstream.js',b'var x=1;')], []),
    'junk_html_tail':      (1, 1, [('pdfstream.js',JS),EVIL,('pdfstream.html',None)], []),
    'gap_in_stream':       (14,14,[('pdfstream.js',JS),('evil.exe',None)], []),   # a nev az /EF hivatkozasbol (pdfparse3 10.4)
    # TNEF (winmail.dat): a tnef_pdfparse3.patch-csel 1 hiba (a pdf vege a TNEF attributum hosszabol), nelkule a
    # kontener maradeka is hiba ("bytes left after EOF"). A patch fuggoben: mindket valtozatot elfogadjuk (l. lent)
    'tnef_container':      (10,10, [('pdfstream.js',JS),EVIL], []),
    'launch':              (0, 0, [('pdfstream.launch',b'/Win /F cmd.exe /P /c calc.exe')], []),
    'rtfd_deep_header':    (10,10, [('pdfstream.js',JS),EVIL], []),     # a header 16K-nal: az Acrobat nem, a PDFKit megnyitja
    'truncated_zero':      (12,12,[('pdfstream.js',JS)], []),    # TRUNCATED + nincs xref
    'truncated_lin':       (12,12,[('pdfstream.js',JS),('pdfstream.dat',PAYLOAD)], []),
    'bogus_xref':          (2, 2, [('pdfstream.js',JS),EVIL], []),
    'misnumbered':         (2, 2, [('pdfstream.js',JS),EVIL], []),
    'xref_zero_unused':    (0, 0, [], []),                   # macOS Quartz: nem hasznalt obj szamok "0 0 n"-nel
    'xref_zero_existing':  (1, 1, [('pdfstream.js',JS),EVIL], []),
    'junk_ws':             (0, 0, [('pdfstream.js',JS),EVIL], []),
    'junk_macbinary':      (5, 5,  [('pdfstream.js',JS),EVIL], []),
    'signed_placeholder':  (0, 0, [('pdfstream.js',JS)], []),
    'leftover_after_eof':  (1, 1, [('pdfstream.js',JS),EVIL], []),
    'truncated_update':    (11,11,[('pdfstream.js',JS),EVIL], []),
    'unterminated_string': (1, 1, [('pdfstream.js',b'after_string()')], []),
    'missing_endobj':      (0, 1, [('pdfstream.js',JS),EVIL], []),   # validate: 1 (2 obj endobj nelkul); content: nem szamolja
    'xref_then_objs':      (0, 0, [('pdfstream.js',JS),EVIL], [('pdfstream.js',b'after_xref()')]),  # nincs az xref-ben
}

# a diagnozisok (TRANSFER, TRUNCATED, JUNK, osszesitett xref hibak) pontosan ezeknel a mintaknal jelenjenek meg
DIAG={'badzlib':['CORRUPT: 1 streams'],'xrefstm_corrupt':['CORRUPT: 1 streams'],'transfer_crlf':['TRANSFER: LF -> CRLF'],'transfer_crlf_naive':['TRANSFER: LF -> CRLF'],
      'transfer_crlf_textheader':['TRANSFER: LF -> CRLF'],'transfer_lf':['TRANSFER: CRLF -> LF'],'crlf_nostream':['TRANSFER: CRLF -> LF'],'lf_text_crlf':['TRANSFER: LF -> CRLF'],
      'junk_bom':['JUNK: 3 bytes before the %PDF header (UTF-8 BOM)'],'junk_html':['JUNK: 53 bytes before the %PDF header (HTML)'],'junk_html_tail':['JUNK: 80 bytes after %%EOF (HTML)'],'junk_macbinary':['JUNK: 128 bytes before the %PDF header (MacBinary header'],'rtfd_deep_header':['JUNK: 16384 bytes before the %PDF header (RTFD package'],'tnef_container':['JUNK: 3583 bytes before the %PDF header (TNEF container'],
      'truncated_zero':['TRUNCATED:'],'truncated_update':['TRUNCATED:'],'gap_in_stream':['GAP: 1336 bytes missing'],'leftover_after_eof':['JUNK: 260 bytes of leftover PDF data after %%EOF'],'truncated_lin':['TRUNCATED:'],
      'bogus_xref':['XREF: bogus xref table'],'misnumbered':['XREF: 7/7 entries point to other objects']}

# a TRANSFER (sorvege-serules) diagnozis pontosan ezeknel a mintaknal jelenjen meg
TRANSFER={'transfer_crlf':'LF -> CRLF','transfer_crlf_naive':'LF -> CRLF','transfer_crlf_textheader':'LF -> CRLF','transfer_lf':'CRLF -> LF','crlf_nostream':'CRLF -> LF','lf_text_crlf':'LF -> CRLF'}

SAMPLES=gen_samples.gen()

# a TNEF kezeles (tnef_pdfparse3.patch) meg fuggoben: nelkule a JUNK "binary data", es a kontener vege is hiba
if not hasattr(pdfparse,'TNEF_SIG'):
    DIAG['tnef_container']=['JUNK: 3583 bytes before the %PDF header (binary data']

def run(name,validate):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        c,e,errs=pdfparse.parse_pdf(SAMPLES[name],validate=validate)
    return c,e,errs,out.getvalue()

def check(name):
    exp_err,exp_verr,exp_c,hidden=EXPECTED[name]
    c,e,errs,out=run(name,False)
    assert e==exp_err, "%s: errcnt %d != %d  %s"%(name,e,exp_err,errs)
    assert e==pdfparse.score(errs)
    if exp_c==None:
        assert c==None, name
    else:
        got=[(n,d) for d,n in c if n!='pdfstream.urls']   # content: [(data,name),...]  (az URL-listat a test_urls nezi)
        left=list(got)
        for n,d in exp_c+hidden:
            m=[x for x in left if x[0]==n and (d==None or x[1]==d)]
            assert m, "%s: missing content %s  got: %s"%(name,(n,d and d[:40]),[(x[0],x[1][:40]) for x in got])
            left.remove(m[0])
        assert not left, "%s: unexpected content %s"%(name,[(x[0],x[1][:40]) for x in left])
    c,e,errs,out=run(name,True)
    assert e==exp_verr, "%s: validate errcnt %d != %d  %s"%(name,e,exp_verr,errs)
    if exp_c!=None:
        assert sorted(n for d,n in c if n not in ('pdfstream.urls','pdfstream.uri.js'))==sorted(n for n,d in exp_c), "%s: validate content names %s"%(name,c)   # (az URI-kat a test_urls nezi)
        assert all(d==None for d,n in c), "%s: validate modban nem kell adat"%(name)

def test_urls():
    # az URL-lista (egy tetel, soronkent egy, ismetles nelkul, a javascript: URI-k nelkul: azok a pdfstream.uri.js
    # tetelekben). Validate modban ugyanezek a tetelek, de adat nelkul (None).
    def urls(name):
        c,e,errs,out=run(name,False)
        u=[d for d,n in c if n=='pdfstream.urls']
        assert len(u)<=1, name
        return u[0] if u else None
    assert urls('basic')==b'http://evil.example/x\n'
    assert b'https://hidden.example' in urls('objstm')          # objstm-ben rejtett URI is
    assert urls('uri_js')==b"http://example.org/a\n"
    for name in SAMPLES:
        cc,e,errs,out=run(name,False)
        c,e,errs,out=run(name,True)
        U=('pdfstream.urls','pdfstream.uri.js')
        assert sorted(n for d,n in c or [] if n in U)==sorted(n for d,n in cc or [] if n in U), name   # (nem pdf: None)
        assert all(d==None for d,n in c or [] if n in U), name

# az elso oldal szovege (pdf2str.first_page_text)
TEXT={'text_tounicode':'\u00c1rv\u00edzt\u0171r\u0151\nt\u00fck\u00f6rf\u00far\u00f3g\u00e9p',   # Type0 + /ToUnicode (bfchar, bfrange)
      'text_winansi_diff':'Sz\u00e9p \u0151sz \u0171r',          # WinAnsi + /Differences (ohungarumlaut, udblacute)
      'text_tj_spacing':'Hello World\nsecond\nthird\nfourth far',  # TJ kerning, Td / T* / ' sorvege, tavoli Td szokoz
      'text_form_xobject':'inside form',
      'text_inline_image':'after image',                         # az inline kep binaris adata nem nyeli el
      'text_identity_cid':'Unicode identity text',               # /ToUnicode nelkuli Identity-H, kod = Unicode
      'text_gid_cid':'visible',                                  # glif-azonositok /ToUnicode nelkul: kimaradnak
      'text_encrypted':'encrypted',                              # AES, ures felhasznaloi jelszo
      'basic':None,'notpdf':None}                                # (nincs szoveg / nem pdf)

def test_text():
    import pdf2str
    for name,want in TEXT.items():
        with contextlib.redirect_stdout(io.StringIO()):
            c,e,errs,text=pdf2str.parse_pdf_text(SAMPLES[name])
        assert text==want, (name,text)

def test_encrypted():
    # Standard security handler: ures felhasznaloi jelszoval visszafejtve (RC4 / AES-128 / AES-256), a jelszavas nem
    for name,want in [('encrypted_rc4','V2 R3 RC4 128 bit, empty user password'),('encrypted_aes','V4 R4 AES 128 bit, empty user password'),
                      ('encrypted_aes256','V5 R6 AES 256 bit, empty user password'),('encrypted_userpw','user password needed')]:
        for validate in (False,True):
            c,e,errs,out=run(name,validate)
            assert want in out, (name,[l for l in out.split('\n') if l.startswith('CRYPT')])
            if 'empty' in want and not validate:
                assert [d for d,n in c if n=='pdfstream.urls']==[b'http://enc.example/x\n'], name

def test_transfer():
    for name in SAMPLES:
        for validate in [False,True]:
            c,e,errs,out=run(name,validate)
            t=[m for w,m in errs if m.startswith('TRANSFER:')]
            if name in TRANSFER:
                assert len(t)==1 and TRANSFER[name] in t[0] and errs[0][1]==t[0], "%s: %s"%(name,t)
                assert ('binary header comment: NO' in t[0])==(name=='transfer_crlf_textheader'), t[0]
            else:
                assert not t, "%s: false TRANSFER: %s"%(name,t)

def test_eol_repair():
    # szoveges file: a visszaalakitas byte-ra pontosan az eredetit adja; binaris adatnal nem probalkozik
    for bad,ok in [('lf_text_crlf','lf_text_ok'),('crlf_nostream','crlf_nostream_ok'),('transfer_crlf_naive',None),('transfer_lf',None)]:
        p=pdfparse.PDFParser(SAMPLES[bad])
        with contextlib.redirect_stdout(io.StringIO()): p.parse()
        if ok: assert p.eol_fixed and p.d==SAMPLES[ok], bad
        else: assert p.eol_fixed==None, bad

def test_diagnoses():
    for name in SAMPLES:
        for validate in [False,True]:
            c,e,errs,out=run(name,validate)
            got=[m for w,m in errs if m.startswith(('TRANSFER:','TRUNCATED:','JUNK:','GAP:','CORRUPT:','ENCODED:','XREF: bogus')) or re.match(r'XREF: \d+/\d+ entries point to other',m)]
            want=DIAG.get(name,[])
            assert len(got)==len(want) and all(g.startswith(w) for g,w in zip(sorted(got),sorted(want))), "%s: %s != %s"%(name,got,want)

def test_expected_complete():
    assert set(EXPECTED)==set(SAMPLES)

def test_samples():
    for name in EXPECTED: check(name)


if __name__=='__main__':
    test_expected_complete()
    test_urls()
    test_text()
    test_encrypted()
    test_transfer()
    test_eol_repair()
    test_diagnoses()
    fail=0
    for name in EXPECTED:
        try:
            check(name)
            if '-v' in sys.argv: print("OK   ",name)
        except AssertionError as e:
            print("FAIL ",e)
            fail+=1
    print("%d/%d samples OK"%(len(EXPECTED)-fail,len(EXPECTED)))
    sys.exit(1 if fail else 0)
