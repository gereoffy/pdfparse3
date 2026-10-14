#! /usr/bin/env python3

# Az elso oldal szoveges tartalmanak kinyerese (pl. szoveg alapu spamszurohoz), a pdfparse3.PDFParser-re epitve.
# Python 3.8+ (pypy3 is), kulso fuggoseg nelkul.
#
#   import pdfparse3,pdf2str
#   p=pdfparse3.PDFParser(d); p.parse()         # (a content / validate feldolgozas mellett)
#   text=pdf2str.first_page_text(p)             # str, vagy None (nincs kinyerheto szoveg)
#
#   content,errcnt,errors,text=pdf2str.parse_pdf_text(d)    # parse_pdf() + a szoveg egyben
#
#   pdf2str.py file.pdf ...                     # parancssorbol: kiirja az elso oldal szoveget
#
# Ami mukodik: /ToUnicode CMap, WinAnsi / MacRoman / Standard kodolas /Differences-szel (glifnevek), Unicode alapu
# elore definialt CMap-ek (UCS2 / UTF16), form XObject-ek, a titkositott file-ok (ha a parser visszafejtette), sorvegek
# es szokozok a szoveg pozicioja alapjan. Ami nem: kepkent beszkennelt oldal (OCR kellene), /ToUnicode nelkuli CID font
# (Identity-H), sajat kodolasu beagyazott font: ezek karakterei kimaradnak, ill. ertelmetlen szoveg lesz belole
# (minosegkapu nincs: azt a felhasznalo, pl. a tartalomszuro tokenizalasa intezi el).
# A szoveg a content stream sorrendjeben jon (szoveg alapu szureshez eleg; tobbhasabos oldalt nem rendez at).

import re
import sys
import unicodedata

import pdfparse3 as pdfparse
from pdfparse3 import PDFString,num

MAXDEPTH=8          # form XObject egymasba agyazas
MAXOPS=2000000      # operator / oldal (a rajzokon a szoveg elott sok szazezer vonalrajzolo operator is lehet;
                    # pypy-vel kb. 1.5M operator/s, CPythonnal kb. 0.2M/s)
MAXTEXT=100000      # karakter / oldal

# --- glifnev -> Unicode ------------------------------------------------------------------------------------------
# A latin betuk nevei (aacute, ohungarumlaut ...) a Unicode nevbol generalva (Adobe Glyph List szerinti nevek), es
# a gyakori irasjelek / ligaturak kezzel. A uniXXXX / uXXXX[XX] neveket kozvetlenul dekodoljuk.
_ACCENT={'ACUTE':'acute','GRAVE':'grave','CIRCUMFLEX':'circumflex','DIAERESIS':'dieresis','TILDE':'tilde',
         'RING ABOVE':'ring','CEDILLA':'cedilla','CARON':'caron','MACRON':'macron','BREVE':'breve','OGONEK':'ogonek',
         'DOT ABOVE':'dotaccent','DOUBLE ACUTE':'hungarumlaut','STROKE':'slash'}
GLYPHS={}
for _c in list(range(0xC0,0x250)):
    try: _n=unicodedata.name(chr(_c))
    except ValueError: continue
    _m=re.match(r'LATIN (SMALL|CAPITAL) LETTER ([A-Z]) WITH (.*)$',_n)
    if _m and _m.group(3) in _ACCENT:
        _b=_m.group(2).lower() if _m.group(1)=='SMALL' else _m.group(2)
        GLYPHS[_b+_ACCENT[_m.group(3)]]=chr(_c)
for _b in 'ou':   # a magyar o/u ketpontos ekezete: hungarumlaut (AGL) vagy dblacute (egyes fontok)
    GLYPHS[_b+'dblacute']=GLYPHS[_b+'hungarumlaut']
    GLYPHS[_b.upper()+'dblacute']=GLYPHS[_b.upper()+'hungarumlaut']
for _c in range(0x41,0x5B): GLYPHS[chr(_c)]=chr(_c); GLYPHS[chr(_c+32)]=chr(_c+32)
GLYPHS.update({'zero':'0','one':'1','two':'2','three':'3','four':'4','five':'5','six':'6','seven':'7','eight':'8',
    'nine':'9','space':' ','exclam':'!','quotedbl':'"','numbersign':'#','dollar':'$','percent':'%','ampersand':'&',
    'quotesingle':"'",'parenleft':'(','parenright':')','asterisk':'*','plus':'+','comma':',','hyphen':'-',
    'period':'.','slash':'/','colon':':','semicolon':';','less':'<','equal':'=','greater':'>','question':'?',
    'at':'@','bracketleft':'[','backslash':'\\','bracketright':']','asciicircum':'^','underscore':'_','grave':'`',
    'braceleft':'{','bar':'|','braceright':'}','asciitilde':'~','quoteleft':'\u2018','quoteright':'\u2019',
    'quotedblleft':'\u201c','quotedblright':'\u201d','quotesinglbase':'\u201a','quotedblbase':'\u201e',
    'guillemotleft':'\u00ab','guillemotright':'\u00bb','guilsinglleft':'\u2039','guilsinglright':'\u203a',
    'endash':'\u2013','emdash':'\u2014','minus':'\u2212','bullet':'\u2022','ellipsis':'\u2026','periodcentered':'\u00b7',
    'fi':'fi','fl':'fl','ff':'ff','ffi':'ffi','ffl':'ffl','germandbls':'\u00df','ae':'\u00e6','AE':'\u00c6',
    'oe':'\u0153','OE':'\u0152','oslash':'\u00f8','Oslash':'\u00d8','eth':'\u00f0','Eth':'\u00d0','thorn':'\u00fe',
    'Thorn':'\u00de','dotlessi':'\u0131','lslash':'\u0142','Lslash':'\u0141','Euro':'\u20ac','euro':'\u20ac',
    'copyright':'\u00a9','registered':'\u00ae','trademark':'\u2122','degree':'\u00b0','section':'\u00a7',
    'paragraph':'\u00b6','plusminus':'\u00b1','multiply':'\u00d7','divide':'\u00f7','nbspace':'\u00a0',
    'exclamdown':'\u00a1','questiondown':'\u00bf','cent':'\u00a2','sterling':'\u00a3','yen':'\u00a5',
    'currency':'\u00a4','brokenbar':'\u00a6','dieresis':'\u00a8','ordfeminine':'\u00aa','ordmasculine':'\u00ba',
    'logicalnot':'\u00ac','sfthyphen':'\u00ad','macron':'\u00af','acute':'\u00b4','mu':'\u00b5','cedilla':'\u00b8',
    'onesuperior':'\u00b9','twosuperior':'\u00b2','threesuperior':'\u00b3','onequarter':'\u00bc','onehalf':'\u00bd',
    'threequarters':'\u00be','dagger':'\u2020','daggerdbl':'\u2021','perthousand':'\u2030','florin':'\u0192',
    'circumflex':'\u02c6','tilde':'\u02dc','caron':'\u02c7','breve':'\u02d8','ring':'\u02da','ogonek':'\u02db',
    'hungarumlaut':'\u02dd','dotaccent':'\u02d9','fraction':'\u2044','tab':'\t'})

def glyph_unicode(name):
    if name in GLYPHS: return GLYPHS[name]
    n=name.split('.')[0].split('_')[0]   # (a.sc, f_f_i: az elso komponens / alap)
    if n in GLYPHS: return GLYPHS[n]
    m=re.match(r'uni([0-9A-Fa-f]{4})+$',n)
    if m:
        try: return ''.join(chr(int(n[i:i+4],16)) for i in range(3,len(n),4))
        except ValueError: return None
    m=re.match(r'u([0-9A-Fa-f]{4,6})$',n)
    if m:
        try: return chr(int(m.group(1),16))
        except ValueError: return None
    return None

# --- egybajtos kodolasok ---------------------------------------------------------------------------------------
def _codec_table(codec):
    t={}
    for c in range(256):
        try: t[c]=bytes([c]).decode(codec)
        except UnicodeDecodeError: pass
    return t
WINANSI=_codec_table('cp1252')
MACROMAN=_codec_table('mac_roman')
# StandardEncoding: az ASCII resz (a ' es ` helyen tipografiai idezojel) + a felso tartomany gyakori glifjei
STANDARD={c:chr(c) for c in range(32,127)}
STANDARD.update({0x27:'\u2019',0x60:'\u2018',0xA1:'\u00a1',0xA2:'\u00a2',0xA3:'\u00a3',0xA4:'\u2044',0xA5:'\u00a5',
    0xA6:'\u0192',0xA7:'\u00a7',0xA8:'\u00a4',0xA9:"'",0xAA:'\u201c',0xAB:'\u00ab',0xAC:'\u2039',0xAD:'\u203a',
    0xAE:'fi',0xAF:'fl',0xB1:'\u2013',0xB2:'\u2020',0xB3:'\u2021',0xB4:'\u00b7',0xB6:'\u00b6',0xB7:'\u2022',
    0xB8:'\u201a',0xB9:'\u201e',0xBA:'\u201d',0xBB:'\u00bb',0xBC:'\u2026',0xBD:'\u2030',0xBF:'\u00bf',0xC1:'`',
    0xC2:'\u00b4',0xC3:'\u02c6',0xC4:'\u02dc',0xC5:'\u00af',0xC6:'\u02d8',0xC7:'\u02d9',0xC8:'\u00a8',0xCA:'\u02da',
    0xCB:'\u00b8',0xCD:'\u02dd',0xCE:'\u02db',0xCF:'\u02c7',0xD0:'\u2014',0xE1:'\u00c6',0xE3:'\u00aa',0xE8:'\u0141',
    0xE9:'\u00d8',0xEA:'\u0152',0xEB:'\u00ba',0xF1:'\u00e6',0xF5:'\u0131',0xF8:'\u0142',0xF9:'\u00f8',0xFA:'\u0153',
    0xFB:'\u00df'})
ENCODINGS={b'/WinAnsiEncoding':WINANSI,b'/MacRomanEncoding':MACROMAN,b'/StandardEncoding':STANDARD,
           b'/PDFDocEncoding':WINANSI}

# --- ToUnicode CMap ---------------------------------------------------------------------------------------------
def _hexbytes(t):
    return t.get() if type(t)==PDFString else None

def _utf16(b):
    try: return b.decode('utf-16-be')
    except UnicodeDecodeError: return b.decode('utf-16-be','replace')

# return: ({kod (int): unicode str}, {kodhossz byte-ban, ...})
def parse_cmap(data):
    m={}
    lens=set()
    for op,args in pdfparse.content_ops(data):
      try:    # (egy hibas blokk ne vigye el a tobbit)
        if op==b'endcodespacerange':
            for i in range(0,len(args)-1,2):
                a=_hexbytes(args[i])
                if a: lens.add(len(a))
        elif op==b'endbfchar':
            for i in range(0,len(args)-1,2):
                s,d=_hexbytes(args[i]),_hexbytes(args[i+1])
                if s is None: continue
                if d is not None: m[int.from_bytes(s,'big')]=_utf16(d)
                elif type(args[i+1])==bytes and args[i+1].startswith(b'/'):   # (glifnev cel)
                    u=glyph_unicode(args[i+1][1:].decode('latin-1'))
                    if u: m[int.from_bytes(s,'big')]=u
                lens.add(len(s))
        elif op==b'endbfrange':
            for i in range(0,len(args)-2,3):
                s,e,d=_hexbytes(args[i]),_hexbytes(args[i+1]),args[i+2]
                if s is None or e is None: continue
                a,b=int.from_bytes(s,'big'),int.from_bytes(e,'big')
                if b<a or b-a>65535: continue
                lens.add(len(s))
                if type(d)==list:
                    for k,x in enumerate(d[:b-a+1]):
                        x=_hexbytes(x)
                        if x is not None: m[a+k]=_utf16(x)
                elif type(d)==PDFString:
                    d=d.get()
                    if not d: continue
                    base=int.from_bytes(d,'big')
                    for k in range(b-a+1):
                        if base+k>=1<<(8*len(d)): break   # (a cel nem fer el a string hosszaban: hibas tartomany vege)
                        m[a+k]=_utf16((base+k).to_bytes(len(d),'big'))
      except (ValueError,OverflowError,TypeError,IndexError):
        continue
    return m,lens

# --- fontok ------------------------------------------------------------------------------------------------------
class Font():
    def __init__(self,p,fd):
        self.codelen=1
        self.map={}        # kod -> unicode
        self.known=False   # van-e hasznalhato lekepezes
        self.widths={}     # kod -> szelesseg (1/1000 em)
        self.dw=500
        self.unicode_cmap=None
        self.identity=False  # /ToUnicode nelkuli Identity-H, de a kod maga a Unicode (l. _identity_fonts)
        self.codes=[]        # (a /ToUnicode nelkuli Type0 font kodjai az ellenorzeshez)
        fd=fd if type(fd)==dict else {}
        sub=fd.get(b'/Subtype')
        tus=None
        if type(fd.get(b'/ToUnicode'))==tuple: tus=p.get_obj(fd[b'/ToUnicode'][1])[1]
        if sub==b'/Type0':
            enc=fd.get(b'/Encoding')
            self.codelen=2
            if type(enc)==bytes and re.match(rb'/Uni.*(UCS2|UTF16)-[HV]$',enc): self.unicode_cmap='utf-16-be'
            desc=p.deref(fd.get(b'/DescendantFonts'))
            cf=p.deref(desc[0]) if type(desc)==list and desc else None
            if type(cf)==dict:
                self.dw=num(p.deref(cf.get(b'/DW')),1000)
                w=p.deref(cf.get(b'/W'))
                if type(w)==list: self._cid_widths(p,w)
        else:
            enc=p.deref(fd.get(b'/Encoding'))
            base=None
            diffs=None
            if type(enc)==bytes: base=ENCODINGS.get(enc)
            elif type(enc)==dict:
                base=ENCODINGS.get(enc.get(b'/BaseEncoding'))
                diffs=p.deref(enc.get(b'/Differences'))
            bf=fd.get(b'/BaseFont')
            symbolic=type(bf)==bytes and re.search(rb'Symbol|Dingbats|Wingdings',bf)
            if base is None and not symbolic:
                # (beepitett kodolas: a nem beagyazott alapfontoknal Standard, a TrueType-nal tobbnyire WinAnsi)
                base=WINANSI if sub==b'/TrueType' else STANDARD
            if base: self.map.update(base)
            if type(diffs)==list:
                code=0
                for x in diffs:
                    if type(x)==int: code=x
                    elif type(x)==bytes and x.startswith(b'/'):
                        u=glyph_unicode(x[1:].decode('latin-1'))
                        if u is not None: self.map[code]=u
                        else: self.map.pop(code,None)
                        code+=1
            self.known=bool(self.map) and not symbolic
            fc=num(p.deref(fd.get(b'/FirstChar')),0)
            w=p.deref(fd.get(b'/Widths'))
            if type(w)==list:
                for k,x in enumerate(w): self.widths[int(fc)+k]=num(p.deref(x),0)
        if tus is not None:
            try:
                m,lens=parse_cmap(bytes(tus.decode()))
            except Exception:
                m,lens={},set()
            if m:
                if sub==b'/Type0':
                    self.map=m
                    if lens and len(lens)==1: self.codelen=lens.pop()
                else: self.map.update(m)
                self.known=True
        if self.unicode_cmap and not tus: self.known=True
        self.cid_nomap=sub==b'/Type0' and not self.map and not self.unicode_cmap

    def _cid_widths(self,p,w):
        i=0
        while i<len(w) and len(self.widths)<100000:
            a=p.deref(w[i])
            if type(a)!=int: break
            b=p.deref(w[i+1]) if i+1<len(w) else None
            if type(b)==list:
                for k,x in enumerate(b): self.widths[a+k]=num(p.deref(x),0)
                i+=2
            elif i+2<len(w) and type(b)==int and b-a<65536:
                for c in range(a,b+1): self.widths[c]=num(p.deref(w[i+2]),0)
                i+=3
            else: break

    # byte-ok -> [(kod, unicode vagy None), ...]
    def decode(self,b):
        out=[]
        if (self.unicode_cmap or self.identity) and not self.map:
            for i in range(0,len(b)-1,2):
                c=int.from_bytes(b[i:i+2],'big')
                out.append((c,chr(c) if c<0xD800 or c>0xDFFF else None))
            return out
        L=self.codelen
        for i in range(0,len(b)-L+1,L):
            c=int.from_bytes(b[i:i+L],'big')
            out.append((c,self.map.get(c)))
        return out

    def width(self,c):
        return self.widths.get(c,self.dw)

# --- az oldal ----------------------------------------------------------------------------------------------------
def first_page(p):
    root=p.deref(('R',p.root)) if p.root else None
    if type(root)!=dict and p.catalog: root=p.get_obj(p.catalog)[0]
    if type(root)!=dict: return None,None
    node=p.deref(root.get(b'/Pages'))
    res=None
    k=0
    while type(node)==dict and node.get(b'/Type')!=b'/Page' and b'/Kids' in node and k<64:
        if b'/Resources' in node: res=node[b'/Resources']
        kids=p.deref(node.get(b'/Kids'))
        if type(kids)!=list or not kids: return None,None
        node=p.deref(kids[0])
        k+=1
    if type(node)!=dict: return None,None
    if b'/Resources' in node: res=node[b'/Resources']
    return node,p.deref(res)

def _stream_data(p,ref):
    if type(ref)==tuple:
        s=p.get_obj(ref[1])[1]
        if s is not None: return bytes(s.decode())
    return b''

def _mul(m,n):
    a,b,c,d,e,f=m
    A,B,C,D,E,F=n
    return (a*A+b*C,a*B+b*D,c*A+d*C,c*B+d*D,e*A+f*C+E,e*B+f*D+F)

class _Page():
    def __init__(self,p,maxops=MAXOPS):
        self.p=p
        self.maxops=maxops
        self.out=[]
        self.nchars=0
        self.unknown=0
        self.fonts={}
        self.lastx=None
        self.lasty=None
        self.nops=0

    def font(self,res,name):
        if type(name)!=bytes: return None   # (hibas operandus: lista / dict / szam)
        fonts=self.p.deref(res.get(b'/Font')) if type(res)==dict else None
        ref=fonts.get(name) if type(fonts)==dict else None
        key=ref[1] if type(ref)==tuple else id(ref)
        if key not in self.fonts: self.fonts[key]=Font(self.p,self.p.deref(ref))
        return self.fonts[key]

    def emit(self,s):
        if self.nchars<MAXTEXT:
            self.out.append(s)
            self.nchars+=len(s)

    def run(self,data,res,ctm,depth,seen):
        st=dict(font=None,fs=1.0,tc=0.0,tw=0.0,th=1.0,tl=0.0,rise=0.0)
        stack=[]
        tm=tlm=(1,0,0,1,0,0)
        for op,a in pdfparse.content_ops(data,self.maxops):
            self.nops+=1
            if self.nops>self.maxops or self.nchars>=MAXTEXT: break
            if op==b'q': stack.append((dict(st),ctm))
            elif op==b'Q':
                if stack: st,ctm=stack.pop()
            elif op==b'cm' and len(a)>=6: ctm=_mul(tuple(num(x) for x in a[:6]),ctm)
            elif op==b'BT': tm=tlm=(1,0,0,1,0,0)
            elif op==b'Tf' and len(a)>=2:
                st['font']=self.font(res,a[0])
                st['fs']=num(a[1],1) or 1
            elif op==b'Tc' and a: st['tc']=num(a[0])
            elif op==b'Tw' and a: st['tw']=num(a[0])
            elif op==b'Tz' and a: st['th']=num(a[0],100)/100.0
            elif op==b'TL' and a: st['tl']=num(a[0])
            elif op==b'Ts' and a: st['rise']=num(a[0])
            elif op in (b'Td',b'TD') and len(a)>=2:
                if op==b'TD': st['tl']=-num(a[1])
                tm=tlm=_mul((1,0,0,1,num(a[0]),num(a[1])),tlm)
            elif op==b'Tm' and len(a)>=6: tm=tlm=tuple(num(x) for x in a[:6])
            elif op==b'T*': tm=tlm=_mul((1,0,0,1,0,-st['tl']),tlm)
            elif op in (b'Tj',b"'",b'"',b'TJ'):
                if op==b"'" or op==b'"':
                    if op==b'"' and len(a)>=3:
                        st['tw'],st['tc']=num(a[0]),num(a[1])
                    tm=tlm=_mul((1,0,0,1,0,-st['tl']),tlm)
                items=a[-1] if a else None
                if op!=b'TJ': items=[items]
                if type(items)!=list: continue
                for x in items:
                    if type(x)==PDFString:
                        tm=self.show(x.get(),st,tm,ctm)
                    elif x is not None and not isinstance(x,PDFString):
                        n=num(x,None)
                        if n is None: continue
                        tm=_mul((1,0,0,1,-n/1000.0*st['fs']*st['th'],0),tm)
                        if n<-180: self.space()
            elif op==b'Do' and a and depth<MAXDEPTH:
                xo=self.p.deref(res.get(b'/XObject')) if type(res)==dict else None
                ref=xo.get(a[0]) if type(xo)==dict and type(a[0])==bytes else None
                if type(ref)!=tuple or ref[1] in seen: continue
                d,s=self.p.get_obj(ref[1])
                if type(d)!=dict or d.get(b'/Subtype')!=b'/Form' or s is None: continue
                m=d.get(b'/Matrix')
                m=tuple(num(x) for x in m[:6]) if type(m)==list and len(m)>=6 else (1,0,0,1,0,0)
                r=self.p.deref(d.get(b'/Resources'))
                self.run(bytes(s.decode()),r if type(r)==dict else res,_mul(m,ctm),depth+1,seen|{ref[1]})
                self.lastx=self.lasty=None

    def space(self):
        if self.out and not self.out[-1].endswith((' ','\n')): self.emit(' ')

    def show(self,b,st,tm,ctm):
        f=st['font']
        if f is None: return tm
        m=_mul(tm,ctm)
        x,y=m[4],m[5]
        scale=(abs(m[2])+abs(m[3])) or 1
        size=st['fs']*scale
        if self.lasty is not None:
            if abs(y-self.lasty)>0.5*size: self.emit('\n')
            elif x-self.lastx>0.15*size: self.space()
            elif x<self.lastx-2*size: self.space()   # (visszaugras: masik hasab / atfedes)
        if f.cid_nomap and len(f.codes)<20000: f.codes+=[int.from_bytes(b[i:i+2],'big') for i in range(0,len(b)-1,2)]
        for c,u in f.decode(b):
            if u is None:
                self.unknown+=1
                u='\ufffd'
            self.emit(u)
            w=f.width(c)/1000.0*st['fs']+st['tc']+(st['tw'] if f.codelen==1 and c==32 else 0)
            tm=_mul((1,0,0,1,w*st['th'],0),tm)
        m=_mul(tm,ctm)
        self.lastx,self.lasty=m[4],m[5]
        return tm

# /ToUnicode nelkuli Identity-H font: egyes irok a Unicode kodot hasznaljak CID-kent (ilyenkor a kod = a karakter).
# Csak akkor fogadjuk el, ha a kodok annak latszanak: van koztuk szokoz (0x0020, a glif-azonositos kodolasnal a szokoz
# tobbnyire a 3-as), es szinte mind nyomtathato karakter. return: volt-e ilyen font
def _identity_fonts(fonts):
    found=False
    for f in fonts.values():
        if not f.cid_nomap or len(f.codes)<20: continue
        sp=f.codes.count(0x20)
        ok=sum(1 for c in f.codes if c>=0x20 and not 0x7F<=c<=0x9F and not 0xD800<=c<=0xDFFF and unicodedata.category(chr(c))[0] in 'LNPSZ')
        if sp>=0.03*len(f.codes) and ok>=0.95*len(f.codes):
            f.identity=True
            found=True
    return found

# a kinyert szoveg rendbetetele: az ismeretlen kodu karakterek (pl. szimbolum font, /ToUnicode nelkuli CID font)
# kimaradnak, a kitolto sorok (....... ______) es a whitespace-ek osszevonva. Minosegkapu nincs: hogy ertelmes szoveg-e,
# azt a felhasznalo (pl. a tartalomszuro tokenizalasa) donti el. None: nincs szoveg.
def _clean(text):
    t=text.replace('\ufffd','')
    t=re.sub(r'([^\w\s])\1{3,}',r'\1\1\1',t)
    t=re.sub(r'[ \t\u00a0]+',' ',t)
    t=re.sub(r' *\n[ \n]*','\n',t).strip()
    return t or None

def first_page_text(p,maxops=MAXOPS):
    try:
        page,res=first_page(p)
        if page is None: return None
        c=page.get(b'/Contents')
        data=b'\n'.join(_stream_data(p,x) for x in (p.deref(c) if type(p.deref(c))==list else [c]))
        res=res if type(res)==dict else {}
        pg=_Page(p,maxops)
        pg.run(data,res,(1,0,0,1,0,0),0,set())
        if _identity_fonts(pg.fonts):
            fonts=pg.fonts
            pg=_Page(p,maxops)
            pg.fonts=fonts
            pg.run(data,res,(1,0,0,1,0,0),0,set())
        return _clean(''.join(pg.out))
    except Exception:   # (szandekosan rontott pdf se allitsa le a hivot: a szoveg ilyenkor nincs)
        return None

# parse_pdf() + az elso oldal szovege egy menetben
def parse_pdf_text(d,debug=False,validate=False):
    p=pdfparse.PDFParser(d,debug,validate)
    if not p.parse(): return None,99,[(99,"not pdf")],None
    return p.content,pdfparse.score(p.errors),p.errors,first_page_text(p)


if __name__=='__main__':
    import io,contextlib
    for fn in sys.argv[1:]:
        d=open(fn,'rb').read()
        with contextlib.redirect_stdout(io.StringIO()):
            c,e,errs,text=parse_pdf_text(d)
        print("=================== %s ====================="%(fn))
        print(text if text is not None else "(no text)")
