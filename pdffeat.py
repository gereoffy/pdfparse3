#! /usr/bin/env pypy3

# PDF jellemzok gyujtese (a nitropdf fejleszteshez valasztott mintakeszlethez): mit hasznal egy PDF a
# megjeleniteshez (fontok, kepszurok, szinterek, arnyalatok, atlatszosag, retegek, annotaciok ...), az elso oldalak
# operatorszama, a Producer / Creator. A pdfscan.py --mode features hivja, a 'select' parancs ebbol valogat.
#
#   pdffeat.py FILE.pdf ...      a jellemzok JSON-kent (kiprobalashoz)
#
# Szemelyes adatot nem gyujt: a /Info-bol csak a Producer es a Creator kerul be (programnevek), a szoveg, a cim, a
# szerzo nem. A file-on beluli nevek (fontnevek) csak a szabvanyos 14 fonthoz valo tartozas szerint.

import re
import sys
import json
import time
import collections

try:
    import pdfparse3 as pdfparse
except ImportError:
    import pdfparse

MAXOPS=2000000      # operator osszesen (az elso oldalak content streamjei), hogy a nagy CAD plotok se fussanak el
MAXPAGES=10         # ennyi oldal operatorait szamoljuk (az oldalszam a /Count-bol, a meret az elso oldalakbol)
MAXOBJS=2000000     # a bejart objektumok
MAXFORMS=200        # a Form XObjectek, amelyeknek az operatorait is szamoljuk (oldalanknti hivasoktol fuggetlenul)

STD14={b'Courier',b'Courier-Bold',b'Courier-Oblique',b'Courier-BoldOblique',b'Helvetica',b'Helvetica-Bold',
       b'Helvetica-Oblique',b'Helvetica-BoldOblique',b'Times-Roman',b'Times-Bold',b'Times-Italic',b'Times-BoldItalic',
       b'Symbol',b'ZapfDingbats',
       b'Arial',b'Arial,Bold',b'Arial,Italic',b'Arial,BoldItalic',b'TimesNewRoman',b'TimesNewRoman,Bold',
       b'TimesNewRoman,Italic',b'TimesNewRoman,BoldItalic',b'CourierNew',b'CourierNew,Bold'}   # (a gyakori alias-ok)
ANNOTS={b'/Text',b'/Link',b'/FreeText',b'/Line',b'/Square',b'/Circle',b'/Polygon',b'/PolyLine',b'/Highlight',
        b'/Underline',b'/Squiggly',b'/StrikeOut',b'/Stamp',b'/Caret',b'/Ink',b'/Popup',b'/FileAttachment',b'/Sound',
        b'/Movie',b'/Widget',b'/Screen',b'/PrinterMark',b'/TrapNet',b'/Watermark',b'/3D',b'/Redact',b'/RichMedia'}

# az operatorok csoportjai (a content stream elemzesehez)
OPS_PATH={b'm',b'l',b'c',b'v',b'y',b'h',b're'}
OPS_PAINT={b'S',b's',b'f',b'F',b'f*',b'B',b'B*',b'b',b'b*',b'n'}
OPS_CLIP={b'W',b'W*'}
OPS_TEXT={b'Tj',b'TJ',b"'",b'"'}
OPS_COLOR={b'g',b'G',b'rg',b'RG',b'k',b'K',b'cs',b'CS',b'sc',b'scn',b'SC',b'SCN'}

# a Producer / Creator: programcsaladokra (a verziok, a cegnevek nelkul). Elso talalat szamit.
FAMILIES=[('autocad',r'autocad|autodesk dwg|acad|pdfplot'),('revit',r'revit'),('archicad',r'archicad|graphisoft'),
    ('microstation',r'microstation|bentley'),('solidworks',r'solidworks'),('inventor',r'inventor'),('catia',r'catia'),
    ('creo',r'creo|pro/engineer|proe'),('siemens-nx',r'\bnx\b|unigraphics|solid edge'),('bricscad',r'bricscad'),
    ('draftsight',r'draftsight'),('progecad',r'progecad'),('allplan',r'allplan'),('vectorworks',r'vectorworks'),
    ('sketchup',r'sketchup'),('qgis',r'qgis'),('arcgis',r'arcgis|arcmap|esri'),('eplan',r'eplan'),('kicad',r'kicad'),
    ('altium',r'altium'),('eagle',r'eagle'),('coreldraw',r'corel'),('visio',r'visio'),('illustrator',r'illustrator'),
    ('inkscape',r'inkscape'),('indesign',r'indesign'),('quark',r'quark'),('photoshop',r'photoshop'),
    ('scribus',r'scribus'),('affinity',r'affinity'),('word',r'microsoft.*word|word for|winword'),
    ('excel',r'microsoft.*excel|excel'),('powerpoint',r'powerpoint'),('word',r'^\s*(microsoft )?word\b'),('office',r'microsoft.*office|microsoft® office'),
    ('libreoffice',r'libreoffice|openoffice|staroffice'),('tex',r'pdftex|xetex|luatex|dvipdf|tex\b|latex|texlive|miktex'),
    ('distiller',r'distiller'),('acrobat',r'acrobat|pdfmaker|adobe pdf library'),('ghostscript',r'ghostscript|gpl gs'),
    ('quartz',r'quartz|mac os x|macos'),('skia',r'skia|chrom'),('cairo',r'cairo'),('wkhtmltopdf',r'wkhtmltopdf|qt '),
    ('itext',r'itext'),('pdfsharp',r'pdfsharp'),('reportlab',r'reportlab'),('tcpdf',r'tcpdf|fpdf'),('dompdf',r'dompdf'),
    ('prince',r'prince'),('fop',r'apache fop|\bfop\b'),('crystal',r'crystal reports'),('sap',r'\bsap\b'),
    ('oracle',r'oracle|bi publisher'),('pdfcreator',r'pdfcreator|primopdf|cutepdf|bullzip|dopdf|pdf24'),
    ('pdf-xchange',r'pdf-xchange|tracker'),('foxit',r'foxit'),('nitro',r'nitro'),('abbyy',r'abbyy|finereader'),
    ('scanner',r'scan|canon|ricoh|xerox|kyocera|konica|epson|brother|sharp|lexmark|toshiba|hp digital|fujitsu'),
    ('ms-print',r'microsoft: print to pdf|microsoft print to pdf'),('report',r'fastreport|quickreport|dxperience|jasperreports|reporting services|report generator|stimulsoft|telerik|list & label|activereports'),('html',r'openhtmltopdf|weasyprint|html'),('browser',r'firefox|safari|edge|opera'),('potrace',r'potrace'),('debenu',r'debenu|quick pdf'),('podofo',r'podofo'),('synactis',r'synactis'),('online2pdf',r'online2pdf'),('signer',r'signer|digitdoc'),
    ('pdflib',r'pdflib|clibpdf|llpdflib'),('ibex',r'ibex'),('pdfium',r'pdfium'),('mupdf',r'mupdf'),('poppler',r'poppler|cairo')]
FAMILIES=[(n,re.compile(r,re.I)) for n,r in FAMILIES]

# a PDF-et iro konyvtarak, nyomtatok: a program a Creator (ha az ismert), nem ez
GENERIC={'distiller','acrobat','ghostscript','quartz','skia','cairo','itext','pdfsharp','reportlab','tcpdf','pdflib',
         'pdfcreator','pdf-xchange','foxit','nitro','poppler','mupdf','pdfium','wkhtmltopdf','fop','ibex','prince',
         'dompdf','office','ms-print','debenu','podofo','synactis','online2pdf','signer','browser','html'}
def generic(f): return f is None or f in GENERIC or f.startswith('other')

# a program: a Creator, ha az ismert program; kulonben a Producer, ha az ismert; kulonben az, amelyik egyaltalan ismert
def pick_family(producer,creator):
    pf,cf=family(producer),family(creator)
    if cf and not generic(cf): return cf
    if pf and not generic(pf): return pf
    for f in (pf,cf):
        if f and not f.startswith('other'): return f
    return cf or pf

def family(s):
    if not s: return None
    for n,r in FAMILIES:
        if r.search(s): return n
    w=re.findall(r'[A-Za-z][A-Za-z0-9\-]+',s)
    return ('other:'+w[0].lower()) if w else 'other'

def text(v):
    if isinstance(v,pdfparse.PDFString): v=v.get()
    if type(v) not in (bytes,bytearray): return None
    v=bytes(v)
    if v.startswith(b'\xfe\xff') or v.startswith(b'\xff\xfe'): s=v.decode('utf-16','replace')
    else: s=v.decode('latin-1')
    s=re.sub(r'[\x00-\x1f]+',' ',s).strip()
    return s[:80] or None

def name(v):
    return v if type(v)==bytes and v.startswith(b'/') else None

# a papirmeret osztalya (mm, a rovidebb es a hosszabb oldal szerint, 3% turessel)
PAPERS=[('A5',148,210),('A4',210,297),('letter',216,279),('legal',216,356),('A3',297,420),('tabloid',279,432),
        ('A2',420,594),('A1',594,841),('A0',841,1189)]
def paper(w,h):
    a,b=sorted((w,h))
    for n,x,y in PAPERS:
        if abs(a-x)<=x*0.03 and abs(b-y)<=y*0.03: return n
    if b>1189*1.03: return 'huge'
    if b>594: return 'large'
    if b>420*1.03: return 'medium'
    return 'small' if b<200 else 'other'

# a JPEG komponensszama a SOF markerbol (a DCT kep szinei: 1 szurke, 3 YCbCr/RGB, 4 CMYK/YCCK)
def jpeg_components(d):
    p=d.find(b'\xff\xd8')
    if p<0: return None
    p+=2
    n=len(d)
    while p+4<n:
        if d[p]!=0xff:
            p+=1
            continue
        m=d[p+1]
        if m in (0xd8,0x01) or 0xd0<=m<=0xd7 or m==0xff:
            p+=1 if m==0xff else 2
            continue
        L=(d[p+2]<<8)|d[p+3]
        if 0xc0<=m<=0xcf and m not in (0xc4,0xc8,0xcc):
            return d[p+9] if p+9<n else None
        if m==0xda: return None
        p+=2+L
    return None

def features(p,maxops=MAXOPS,maxpages=MAXPAGES):
    t0=time.time()
    F={}
    c=collections.Counter
    fonts,cmaps,encodings,images,cs,bpc,shadings,patterns,funcs,annots,bm=c(),c(),c(),c(),c(),c(),c(),c(),c(),c(),c()
    tr=c()
    nobjs=0
    maxpix=0
    producer=creator=None
    ocg=0
    linearized=False
    hdr=p.d[:1024].find(b'%PDF-')
    if hdr>=0: F['ver']=p.d[hdr+5:hdr+8].decode('latin-1','replace')
    F['eofs']=p.d.count(b'%%EOF')   # (inkrementalis mentesek + 1)
    F['xrefstm']=bool(p.xref_stream_oids)
    F['objstm']=len(p.objstm)
    if p.encrypt:
        cr=p.crypt
        F['encrypt']=('R%d %s %d'%(cr['R'],'AES' if 'aes' in (cr['stm'],cr['str']) else 'RC4',8*cr['n'])) if cr else 'password'
    oids=set(p.dom)
    for s in p.objstm.values(): oids|=set(s)
    forms=[]
    for oid in sorted(oids):
        if nobjs>=MAXOBJS: break
        nobjs+=1
        try:
            v,st=p.get_obj(oid)
        except Exception:
            continue
        if type(v)!=dict: continue
        T=v.get(b'/Type')
        S=v.get(b'/Subtype')
        if b'/Producer' in v or b'/Creator' in v:   # (a /Info: a legkesobbi, azaz a legutobbi mentes)
            producer=text(p.deref(v.get(b'/Producer'))) or producer
            creator=text(p.deref(v.get(b'/Creator'))) or creator
        if b'/Linearized' in v: linearized=True
        # fontok
        if T==b'/Font' or (S in (b'/Type1',b'/MMType1',b'/TrueType',b'/Type3',b'/Type0') and b'/BaseFont' in v):
            if S in (b'/CIDFontType0',b'/CIDFontType2'): continue   # (a Type0 leszarmazottja: ott szamoltuk)
            if S==b'/Type0':
                enc=p.deref(v.get(b'/Encoding'))
                if name(enc): cmaps[enc.decode('latin-1')[1:]]+=1
                elif type(enc)==dict or enc is None: cmaps['embedded' if enc is not None else 'none']+=1
                d=p.deref(v.get(b'/DescendantFonts'))
                d=p.deref(d[0]) if type(d)==list and d else None
                sub=d.get(b'/Subtype') if type(d)==dict else None
                fd=p.deref(d.get(b'/FontDescriptor')) if type(d)==dict else None
                kind='Type0/'+(sub.decode('latin-1')[1:] if name(sub) else '?')
            elif S==b'/Type3':
                fonts['Type3']+=1
                continue
            else:
                fd=p.deref(v.get(b'/FontDescriptor'))
                kind=S.decode('latin-1')[1:] if name(S) else '?'
                enc=p.deref(v.get(b'/Encoding'))
                if name(enc): encodings[enc.decode('latin-1')[1:]]+=1
                elif type(enc)==dict:
                    base=enc.get(b'/BaseEncoding')
                    encodings[('Differences+'+base.decode('latin-1')[1:]) if name(base) else 'Differences']+=1
                else: encodings['builtin']+=1
            emb='notembedded'
            if type(fd)==dict:
                if b'/FontFile' in fd: emb='FontFile'
                elif b'/FontFile2' in fd: emb='FontFile2'
                elif b'/FontFile3' in fd:
                    ff,fst=p.get_obj(fd[b'/FontFile3'][1]) if type(fd[b'/FontFile3'])==tuple else (None,None)
                    sub3=ff.get(b'/Subtype') if type(ff)==dict else None
                    emb='FontFile3/'+(sub3.decode('latin-1')[1:] if name(sub3) else '?')
            if emb=='notembedded' and kind in ('Type1','TrueType'):
                bf=v.get(b'/BaseFont')
                bf=bf[1:] if name(bf) else b''
                bf=bf.split(b'+')[-1]   # (a subset elotag)
                emb='std14' if bf in STD14 else 'notembedded'
            fonts[kind+':'+emb]+=1
            continue
        if T==b'/FontDescriptor': continue
        # kepek
        if S==b'/Image' and st is not None:
            flt=st.fmt or []
            f=(flt[-1].decode('latin-1')[1:] if flt and type(flt[-1])==bytes else 'raw')
            f={'DCT':'DCTDecode','Fl':'FlateDecode','CCF':'CCITTFaxDecode','LZW':'LZWDecode','RL':'RunLengthDecode','AHx':'ASCIIHexDecode','A85':'ASCII85Decode'}.get(f,f)
            if f=='DCTDecode':
                try:
                    data=st.data if len(flt)==1 else st.decode()
                    n=jpeg_components(bytes(data[:65536]))
                except Exception:
                    n=None
                f='DCT%s'%(n if n else '?')
            images[f]+=1
            if v.get(b'/ImageMask')==b'true': images['imagemask']+=1
            if b'/SMask' in v: images['smask']+=1
            if b'/Mask' in v: images['mask:'+('stencil' if type(v[b'/Mask'])==tuple else 'colorkey')]+=1
            if type(v.get(b'/BitsPerComponent'))==int: bpc[str(v[b'/BitsPerComponent'])]+=1
            w,h=v.get(b'/Width'),v.get(b'/Height')
            if type(w)==int and type(h)==int: maxpix=max(maxpix,w*h)
            col=p.deref(v.get(b'/ColorSpace'))
            k=colorspace(p,col)
            if k: cs['image:'+k]+=1
            continue
        if S==b'/Form' and st is not None:
            if len(forms)<MAXFORMS: forms.append(st)
            g=p.deref(v.get(b'/Group'))
            if type(g)==dict and g.get(b'/S')==b'/Transparency': tr['group']+=1
            if b'/OC' in v: tr['form:oc']+=1
        # szinterek (a resource-okban nevvel, ill. tombkent bark ahol)
        if b'/ColorSpace' in v and T!=b'/XObject' and type(p.deref(v[b'/ColorSpace']))==dict:
            for x in p.deref(v[b'/ColorSpace']).values():
                k=colorspace(p,p.deref(x))
                if k: cs[k]+=1
        if b'/ShadingType' in v: shadings[str(v[b'/ShadingType'])]+=1
        if b'/PatternType' in v:
            pt=v[b'/PatternType']
            patterns['tiling:'+str(v.get(b'/PaintType')) if pt==1 else 'shading' if pt==2 else str(pt)]+=1
        if b'/FunctionType' in v: funcs[str(v[b'/FunctionType'])]+=1
        if T==b'/ExtGState' or (b'/BM' in v or b'/SMask' in v or b'/CA' in v or b'/ca' in v) and T is None and S is None:
            sm=v.get(b'/SMask')
            if sm is not None and sm!=b'/None': tr['smask']+=1
            b=v.get(b'/BM')
            b=b[0] if type(b)==list and b else b
            if name(b) and b not in (b'/Normal',b'/Compatible'): bm[b.decode('latin-1')[1:]]+=1
            for a in (b'/CA',b'/ca'):
                x=pdfparse.num(v.get(a),1)
                if x<1: tr['alpha']+=1
        if T==b'/OCG': ocg+=1
        if (T==b'/Annot' or S in ANNOTS) and b'/Rect' in v:
            annots[S.decode('latin-1')[1:] if name(S) else '?']+=1
            if b'/AP' in v: annots['with-appearance']+=1
        if T==b'/Catalog':
            if b'/AcroForm' in v:
                af=p.deref(v[b'/AcroForm'])
                F['acroform']=True
                if type(af)==dict and b'/XFA' in af: F['xfa']=True
            ocp=p.deref(v.get(b'/OCProperties'))
            if type(ocp)==dict:
                d=p.deref(ocp.get(b'/D'))
                off=p.deref(d.get(b'/OFF')) if type(d)==dict else None
                F['ocg_off']=len(off) if type(off)==list else 0
    F['objs']=nobjs
    if producer: F['producer']=producer
    if creator: F['creator']=creator
    F['family']=pick_family(producer,creator)
    F['linearized']=linearized
    for k,x in (('fonts',fonts),('cmaps',cmaps),('encodings',encodings),('images',images),('cs',cs),('bpc',bpc),
                ('shadings',shadings),('patterns',patterns),('functions',funcs),('annots',annots),('blend',bm),
                ('transparency',tr)):
        if x: F[k]=dict(x)
    if ocg: F['ocg']=ocg
    if maxpix: F['maxpix']=maxpix
    # oldalak: a darabszam, az elso oldalak merete es operatorai
    try:
        import pdf2str
        pg=pdf2str.pages(p,maxpages)
        root=p.deref(('R',p.root)) if p.root else None
        if type(root)!=dict and p.catalog: root=p.get_obj(p.catalog)[0]
        cnt=p.deref(p.deref(root.get(b'/Pages')).get(b'/Count')) if type(root)==dict and type(p.deref(root.get(b'/Pages')))==dict else None
        F['pages']=cnt if type(cnt)==int else len(pg)
        sizes=c()
        ops=c()
        maxpage=0
        budget=maxops
        for k,(page,res) in enumerate(pg):
            b=page_rect(p,page)
            if b:
                w,h=(b[2]-b[0])*25.4/72,(b[3]-b[1])*25.4/72
                uu=pdfparse.num(p.deref(page.get(b'/UserUnit')),1) or 1
                sizes[paper(w*uu,h*uu)]+=1
                if k==0:
                    F['page_mm']=[round(w*uu),round(h*uu)]
                    rot=p.deref(page.get(b'/Rotate'))
                    if type(rot)==int and rot%360: F['rotate']=rot%360
                    if uu!=1: F['userunit']=uu
            data=b''
            cont=p.deref(page.get(b'/Contents'))
            for r in (cont if type(cont)==list else [page.get(b'/Contents')]):
                try: data+=pdf2str._stream_data(p,r)+b'\n'
                except Exception: pass
            n=count_ops(data,ops,budget)
            budget-=n
            maxpage=max(maxpage,n)
            if budget<=0: break
        for st in forms:   # (a Form XObjectek egyszer, a hivasoktol fuggetlenul)
            if budget<=0: break
            try: n=count_ops(bytes(st.decode()),ops,min(budget,200000))
            except Exception: n=0
            budget-=n
        F['sizes']=dict(sizes)
        F['ops']=dict(ops)
        F['ops_maxpage']=maxpage
        if budget<=0: F['ops_capped']=True
    except Exception as ex:
        F['pages_error']=repr(ex)[:200]
    F['time']=round(time.time()-t0,3)
    return F

# az oldal latszo terulete (a CropBox a MediaBox-szal metszve, oroklessel), mint a pdf2str.page_box, turees nelkul
def page_rect(p,page):
    def inh(key):
        node=page
        for i in range(64):
            if type(node)!=dict: return None
            if key in node: return p.deref(node[key])
            node=p.deref(node.get(b'/Parent'))
    def rect(v):
        if type(v)!=list or len(v)<4: return None
        x=[pdfparse.num(p.deref(t),None) for t in v[:4]]
        if None in x: return None
        r=(min(x[0],x[2]),min(x[1],x[3]),max(x[0],x[2]),max(x[1],x[3]))
        return r if r[2]>r[0] and r[3]>r[1] else None
    m,c=rect(inh(b'/MediaBox')),rect(inh(b'/CropBox'))
    if m and c: c=(max(m[0],c[0]),max(m[1],c[1]),min(m[2],c[2]),min(m[3],c[3]))
    return c if c and c[2]>c[0] and c[3]>c[1] else m

def colorspace(p,v):
    if name(v): return v.decode('latin-1')[1:]
    if type(v)==list and v and name(v[0]):
        k=v[0].decode('latin-1')[1:]
        if k=='ICCBased' and len(v)>1:
            d=p.get_obj(v[1][1])[0] if type(v[1])==tuple else None
            n=d.get(b'/N') if type(d)==dict else None
            return 'ICCBased%s'%(n if type(n)==int else '?')
        if k=='Indexed' and len(v)>1:
            base=colorspace(p,p.deref(v[1]))
            return 'Indexed/'+(base or '?')
        if k=='DeviceN' and len(v)>1:
            n=p.deref(v[1])
            return 'DeviceN%d'%(len(n)) if type(n)==list else 'DeviceN'
        return k
    return None

def count_ops(data,ops,budget):
    n=0
    for op,args in pdfparse.content_ops(data,max(1,budget)):
        n+=1
        if op in OPS_PATH: ops['path']+=1
        elif op in OPS_PAINT: ops['paint']+=1
        elif op in OPS_CLIP: ops['clip']+=1
        elif op in OPS_TEXT: ops['text']+=1
        elif op in OPS_COLOR: ops['color']+=1
        elif op==b'Do': ops['Do']+=1
        elif op==b'sh': ops['sh']+=1
        elif op==b'BI': ops['inline']+=1
        elif op in (b'BDC',b'BMC'):
            if args and args[0]==b'/OC': ops['oc']+=1
        elif op==b'gs': ops['gs']+=1
        elif op==b'd':
            if args and type(args[0])==list and args[0]: ops['dash']+=1
        elif op in (b'd0',b'd1'): ops['type3']+=1
        ops['all']+=1
    return n

if __name__=='__main__':
    import io, contextlib
    for fn in sys.argv[1:]:
        with open(fn,'rb') as f: d=f.read()
        with contextlib.redirect_stdout(io.StringIO()):
            p=pdfparse.PDFParser(d,False,False)
            ok=p.parse()
        print(json.dumps({'file':fn,'features':features(p) if ok else None},ensure_ascii=False))
