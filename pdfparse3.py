#! /usr/bin/python3

# PDF parser: beagyazott JavaScript es csatolmany (EmbeddedFile) kinyerese, es kozben
# konzisztencia ellenorzes (szintaktikai / tomoritesi / xref hibak szamlalasa).
# A korabbi pdfparse2.py es testpdf.py osszevonasa. Megengedo: amit csak tud, feldolgoz,
# a talalt hibakat pedig megszamolja.
#
#   content,errcnt,errors = parse_pdf(data, validate=False)
#       content = [(data,filename),...]    JS eseten filename="pdfstream.js"
#                 validate=True eseten data=None (nem taroljuk a kibontott adatot, csak ellenorzunk)
#       errcnt  = a hibak sulyozott osszege
#       errors  = [(suly,uzenet),...]
#   nem pdf eseten: (None,99,[(99,"not pdf")])

# https://resources.infosecinstitute.com/topic/pdf-file-format-basic-structure/#gref

import os
import sys
import traceback
import zlib
import re

import base64

WHITESPACE=b'\x00\t\n\x0c\r '

def hexdigit(a):
    if a>=0x30 and a<=0x39: return a-0x30
    if a>=0x41 and a<=0x46: return a+10-0x41
    if a>=0x61 and a<=0x66: return a+10-0x61
    return None

# alapertelmezett hibakezelo a lexerhez: csak kiirja
def print_err(msg,n=1):
    print(msg)

# based on https://github.com/py-pdf/pypdf/blob/main/pypdf/filters.py
class LZWDecode:
        def __init__(self, data: bytes) -> None:
            self.STOP = 257
            self.CLEARDICT = 256
            self.data = data
            self.error = None
            self.note = None
            self.bytepos = 0
            self.bitpos = 0
            self.dict = [b''] * 4096
            for i in range(256):
                self.dict[i] = bytes([i])
            self.reset_dict()

        def reset_dict(self) -> None:
            self.dictlen = 258
            self.bitspercode = 9

        def next_code(self) -> int:
            fillbits = self.bitspercode
            value = 0
            while fillbits > 0:
                if self.bytepos >= len(self.data):
                    return -1
                nextbits = self.data[self.bytepos]
                bitsfromhere = 8 - self.bitpos
                bitsfromhere = min(bitsfromhere, fillbits)
                value |= (
                    (nextbits >> (8 - self.bitpos - bitsfromhere))
                    & (0xFF >> (8 - bitsfromhere))
                ) << (fillbits - bitsfromhere)
                fillbits -= bitsfromhere
                self.bitpos += bitsfromhere
                if self.bitpos >= 8:
                    self.bitpos = 0
                    self.bytepos = self.bytepos + 1
            return value

        # hiba eseten a reszben dekodolt adatot adja vissza, a hiba a self.error-ba kerul
        def decode(self) -> bytes:
            cW = self.CLEARDICT
            baos = []
            while True:
                pW = cW
                cW = self.next_code()
                if cW == -1:
                    self.error = "End of buffer reached without LZW stop code"
                    break
                if cW == self.STOP:
                    break
                elif cW == self.CLEARDICT:
                    self.reset_dict()
                elif pW == self.CLEARDICT:
                    if cW >= self.dictlen:
                        self.error = "invalid code %d after clear"%(cW)
                        break
                    baos.append(self.dict[cW])
                else:
                    if self.dictlen >= 4096:
                        self.error = "dictionary overflow (missing clear code)"
                        break
                    if cW < self.dictlen:
                        baos.append(self.dict[cW])
                        p = self.dict[pW] + self.dict[cW][:1]
                        self.dict[self.dictlen] = p
                        self.dictlen += 1
                    elif cW == self.dictlen:
                        p = self.dict[pW] + self.dict[pW][:1]
                        baos.append(p)
                        self.dict[self.dictlen] = p
                        self.dictlen += 1
                    else:
                        self.error = "invalid code %d (dict size %d)"%(cW,self.dictlen)
                        break
                    if (
                        self.dictlen >= (1 << self.bitspercode) - 1
                        and self.bitspercode < 12
                    ):
                        self.bitspercode += 1
            bleft=len(self.data)-self.bytepos
#            print("LZW bytesleft=%d  decoded %d -> %d bytes, %d runs"%(bleft, len(self.data),len(b''.join(baos)),len(baos)))
            # az EOD kod utani adatot a spec szerint figyelmen kivul kell hagyni: csak megjegyzes
            if not self.error and bleft>3: self.note = "%d bytes left in LZW buffer after the EOD code"%(bleft)
            return b''.join(baos)


# return: data,hibauzenet (az ervenytelen karaktereket atugorja)
def ASCIIHexDecode(d):
    p=0
    pend=len(d)
    data=bytearray()
    err=None
    while p<pend:
        if d[p]==0x3E: break # EOD '>'
        if d[p]<=32:  # skip whitespace
            p+=1
            continue
        x=hexdigit(d[p])
        p+=1
        if x==None:
            if not err: err="ASCIIHex: invalid char 0x%02X at %d"%(d[p-1],p-1)
            continue
        while p<pend and d[p]<=32: p+=1  # a ket nibble kozott is lehet whitespace
        y=hexdigit(d[p]) if p<pend else None
        if y==None:
            y=0  # az utolso nibble hianyozhat
        else:
            p+=1
        data.append((x<<4)+y)
    if p>=pend: err=err or "ASCIIHex: missing EOD '>'"
    return data,err

# ASCII85 kibontas, megengedo modon (a python base64.a85decode szigorubb a pdf nezoknel):
#  - a 32 biten tulcsordulo csoportot levagjuk ("Ascii85 overflow", egyes irok ilyet irnak, a nezok elfogadjak)
#  - 'y' (btoa kiterjesztes: 4 szokoz) es az ervenytelen karakterek: atugorjuk / elfogadjuk, megjegyzessel
# return: data,megjegyzes
def ASCII85Decode(d):
    if d[:2]==b'<~': d=d[2:]
    e=d.find(b'~>')
    if e>=0: d=d[:e]
    out=bytearray()
    note=[]
    grp=[]
    for c in d:
        if 33<=c<=117:
            grp.append(c-33)
            if len(grp)==5:
                v=(((grp[0]*85+grp[1])*85+grp[2])*85+grp[3])*85+grp[4]
                if v>0xFFFFFFFF:
                    if 'overflow' not in note: note.append('overflow')
                    v&=0xFFFFFFFF
                out+=v.to_bytes(4,'big')
                grp=[]
        elif c==0x7A and not grp: out+=b'\0\0\0\0'          # z
        elif c==0x79 and not grp:                             # y (btoa)
            out+=b'    '
            if 'y' not in note: note.append('y')
        elif c in WHITESPACE: continue
        elif 'invalid char' not in note: note.append('invalid char')
    if grp:
        n=len(grp)
        if n==1: note.append('1 char in the last group')
        else:
            grp+=[84]*(5-n)
            v=((((grp[0]*85+grp[1])*85+grp[2])*85+grp[3])*85+grp[4])&0xFFFFFFFF
            out+=v.to_bytes(4,'big')[:n-1]
    return out,("ASCII85: "+", ".join(note)) if note else None

def RunLengthDecode(d):
    p=0
    pend=len(d)
    data=bytearray()
    while p<pend:
        n=d[p]
        p+=1
        if n==128: break # EOD
        if n<128:
            data+=d[p:p+n+1]
            p+=n+1
        else:
            if p<pend: data+=bytes([d[p]])*(257-n)
            p+=1
    return data

# raw deflate kibontas (zlib header es checksum nelkul, 32K ablakkal), kis darabokban, hogy hiba eseten is
# megmaradjon, ami kijott. return: data, a deflate adat teljes-e, a deflate adat utani byte-ok
def raw_inflate(d,chunk=4096):
    zo=zlib.decompressobj(-15)
    out=[]
    try:
        for i in range(0,len(d),chunk):
            out.append(zo.decompress(d[i:i+chunk]))
            if zo.eof: break
    except zlib.error:
        pass
    return b''.join(out),zo.eof,zo.unused_data

# zlib kibontas. hiba eseten az addig kibontott adatot adja vissza + hibauzenet
# return: data,hiba,megjegyzes
# Ha a sima zlib hibat ad vagy nem er a vegere, raw deflate-tel is megprobaljuk (ahogy az olvasok), es az adler32
# checksumot kezzel ellenorizzuk:
#  - hianyzo (vagy csak reszben meglevo) checksum: megjegyzes. Valid pdf-ekben is elofordul (foleg pici streameknel),
#    az olvasok nem ellenorzik. (2026-09: a tesztkorpusz osszes ilyen streamje ilyen volt, PDFKit-tel megnyiltak.)
#  - tul kicsi ablakmeret a zlib headerben ("invalid distance too far back"), de 32K ablakkal jo, es a checksum is
#    stimmel: megjegyzes (az olvasok 32K ablakkal bontanak)
#  - zlib header nelkuli raw deflate: megjegyzes
#  - "00 00 FF FF" a vegen (sync flush, de a folyamot nem zartak le): az adat teljes, megjegyzes
#  - rossz checksum: hiba, mert az adat serulesere utalhat (a deflate-ben nincs mas ellenorzes)
def inflate(d):
    if d.strip(WHITESPACE)==b'': return b'',None,None   # ures stream (csak a sorvege van meg az endstream elott)
    zo=zlib.decompressobj()
    out=[]
    err=None
    try:
        # darabokban, hogy hiba eseten a hiba elotti resz megmaradjon
        for i in range(0,len(d),4096):
            out.append(zo.decompress(d[i:i+4096]))
            if zo.eof: break
        out.append(zo.flush())
    except zlib.error as e:
        err=str(e)
    dd=b''.join(out)
    if not err and zo.eof: return dd,None,None
    hdr_ok=len(d)>=2 and d[0]&15==8 and (d[0]*256+d[1])%31==0
    for data,skip in ([(d[2:],2)] if hdr_ok else [])+[(d,0)]:
        rd,reof,unused=raw_inflate(data)
        if not reof: continue
        if skip==0 and hdr_ok: continue
        if skip==0: return rd,None,"ZLIB: raw deflate without zlib header (decoded %d bytes)"%(len(rd))
        tail=unused.rstrip(WHITESPACE) if len(unused.rstrip(WHITESPACE))<4 else unused[:4]
        if len(tail)<4: return rd,None,"ZLIB: missing adler32 checksum (decoded %d/%d bytes)"%(len(rd),len(d))
        if zlib.adler32(rd)!=int.from_bytes(tail[:4],'big'):
            return rd,"ZLIB: adler32 checksum mismatch, deflate data complete (decoded %d bytes)"%(len(rd)),None
        if err and 'too far back' in err: return rd,None,"ZLIB: window size in zlib header too small, OK with 32K window (decoded %d bytes)"%(len(rd))
        return rd,None,"ZLIB: %s, but raw deflate and checksum OK (decoded %d bytes)"%(err or 'not finished',len(rd))
    if not err and d.rstrip(WHITESPACE).endswith(b'\x00\x00\xff\xff'):
        return dd,None,"ZLIB: stream ends with sync flush, no final block (decoded %d/%d bytes)"%(len(dd),len(d))
    # mentsuk ami mentheto
    for data in ([d[2:]] if hdr_ok else [])+[d]:
        rd,reof,unused=raw_inflate(data,256)
        if len(rd)>len(dd): dd=rd
    if err: return dd,"ZLIB: %s (decoded %d bytes)"%(err,len(dd)),None
    return dd,"ZLIB: truncated stream? decoded %d/%d bytes"%(len(dd),len(d)),None

# a deflate adat teljes-e (raw deflate, a zlib header es az adler32 checksum nelkul)
def deflate_complete(d):
    return raw_inflate(d[2:])[1]

# PNG (10-15) es TIFF (2) predictor visszaalakitasa (/DecodeParms)
def unpredict(d,parms):
    pred=parms.get(b'/Predictor',1)
    if type(pred)!=int or pred<=1: return d
    colors=parms.get(b'/Colors',1)
    bpc=parms.get(b'/BitsPerComponent',8)
    cols=parms.get(b'/Columns',1)
    bpp=max(1,(colors*bpc+7)//8)
    rowlen=(colors*bpc*cols+7)//8
    out=bytearray()
    if pred==2:
        if bpc!=8: raise ValueError("TIFF predictor with BitsPerComponent=%d not supported"%(bpc))
        for r in range(0,len(d),rowlen):
            row=bytearray(d[r:r+rowlen])
            for i in range(bpp,len(row)): row[i]=(row[i]+row[i-bpp])&0xFF
            out+=row
        return out
    if pred<10: raise ValueError("unknown predictor %d"%(pred))
    prev=bytearray(rowlen)
    for r in range(0,len(d),rowlen+1):
        ft=d[r]
        row=bytearray(d[r+1:r+1+rowlen])
        if len(row)<rowlen: raise ValueError("PNG predictor: truncated row (%d/%d bytes)"%(len(row),rowlen))
        if ft==1:
            for i in range(bpp,rowlen): row[i]=(row[i]+row[i-bpp])&0xFF
        elif ft==2:
            for i in range(rowlen): row[i]=(row[i]+prev[i])&0xFF
        elif ft==3:
            for i in range(rowlen): row[i]=(row[i]+(((row[i-bpp] if i>=bpp else 0)+prev[i])>>1))&0xFF
        elif ft==4:
            for i in range(rowlen):
                a=row[i-bpp] if i>=bpp else 0
                b=prev[i]
                c=prev[i-bpp] if i>=bpp else 0
                pa=abs(b-c); pb=abs(a-c); pc=abs(a+b-2*c)
                row[i]=(row[i]+(a if pa<=pb and pa<=pc else (b if pb<=pc else c)))&0xFF
        elif ft!=0:
            raise ValueError("PNG predictor: invalid filter type %d"%(ft))
        out+=row
        prev=row
    return out

# special string object
class PDFString():
    def __init__(self,d):
        self.data=d
    def get(self):
        return bytes(self.data)
    # print formatuma:
    def __repr__(self):
        return str(self.data[0:16])

class PDFStream():
    def __init__(self,d,f=None,parms=None):
        self.data=d
        self.fmt=f
        self.parms=parms
        self.error=None
        self.note=None
        self.pos=None       # az adat kezdete, az endstream pozicioja es a /Length (ha kozvetlen ertek)
        self.endpos=None    # a file-ban (a sorvege-serules felismeresehez)
        self.declared=None
    def add_note(self,n):
        if n: self.note=self.note+"; "+n if self.note else n
    # nem dob exceptiont: hiba eseten az addig dekodolt adatot adja vissza, a (legelso) hiba a self.error-ba kerul
    # predictor=True: a /DecodeParms predictort is visszaalakitja (xref, objstm - kepeknel lassu es felesleges)
    def decode(self,predictor=False):
        self.error=None
        self.note=None
        d=self.data
        for f in self.fmt or []:
            e=None
            try:
                if f in [b'/ASCII85Decode',b'/A85']:
                    d,n=ASCII85Decode(d)
                    self.add_note(n)
                elif f in [b'/ASCIIHexDecode',b'/AHx']: d,e=ASCIIHexDecode(d)
                elif f in [b'/FlateDecode',b'/Fl']:
                    d,e,n=inflate(d)
                    self.add_note(n)
                elif f in [b'/LZWDecode',b'/LZW']:
                    lz=LZWDecode(d)
                    d=lz.decode()
                    if lz.error: e="LZW: "+lz.error
                    if lz.note: self.add_note("LZW: "+lz.note)
                elif f in [b'/RunLengthDecode',b'/RL']: d=RunLengthDecode(d)
                elif f in [b'/CCITTFaxDecode',b'/CCF',b'/JBIG2Decode',b'/JPXDecode',b'/DCTDecode',b'/DCT']:  # picture formats
                    break
                elif f!=b'/Crypt':
                    print("STREAM: unsupported format "+str(f))
            except Exception as ex:
                e="%s: %s"%(str(f),repr(ex))
            if e and not self.error: self.error=e
        if predictor and self.parms:
            try:
                d=unpredict(d,self.parms)
            except Exception as ex:
                if not self.error: self.error="Predictor: "+repr(ex)
        return d
    # print formatuma:
    def __repr__(self):
#        return str(self.data[0:16])
        unc=0 #len(self.decode())
        try:
            return "Stream(%d/%d):%s"%(len(self.data),unc,str(b''.join(self.fmt)) )
        except Exception:
            return "Stream(%d/%d)"%(len(self.data),unc)

def parse_pdf_param(d,p,pend,err=print_err):

    while p<pend:

        # 'endobj' utan nem mindig van whitespace... :(
        if p+6<=pend and d[p:p+6]==b'endobj':
            p+=6
            return p,b'endobj'

        c=d[p]
        p+=1
        if c<=32: continue # ignore whitespace

        t=chr(c)

        if c in [0x3C,0x3E] and p<pend and d[p]==c: # <<DICT>>
            p+=1
            return p,t #objs.append(t)

        if c in [41, 62, 91,93, 123,125]:  #  ) > [] {}
            return p,t

        data=bytearray()

        if c==0x25:  # %COMMENT
            if p+4<=pend and d[p:p+4]==b'%EOF':
                p+=4
#                print("EOF!!")
#                return p,None
                return p,b'%%EOF'
            data.append(c) # % jel az elejere!
            while p<pend:
                c=d[p]
                p+=1
                if c==10 or c==13:
                    break
                data.append(c)
            #print(data)
            continue # ignore comment

        if c==0x3C:          # <HEXSTRING>
            nib=None
            closed=False
            while p<pend:
                c=d[p]
                p+=1
                if c<=0x20: continue # skip ws/newline (a ket nibble kozott is!)
                if c==0x3E: # > = end of string
                    closed=True
                    break
                x=hexdigit(c)
                if x==None:
                    err("Invalid HEX string char 0x%02X at 0x%X"%(c,p-1))
                    p-=1 # fixme?
                    break
                if nib==None:
                    nib=x
                else:
                    data.append((nib<<4)+x)
                    nib=None
            if nib!=None: data.append(nib<<4)  # az utolso nibble hianyozhat is...
            if not closed and p>=pend: err("Unterminated HEX string at end of data")
            return p,PDFString(data)

        if c==0x28:   # (string)
            s0=p
            in_str=1
            while p<pend:
                c=d[p]
                p+=1
                if c==0x28:   # (
                    in_str+=1
                elif c==0x29:   # )
                    in_str-=1
                    if in_str<=0: break
                elif c==0x0D: # sorvege (CR, CRLF) -> LF
                    if p<pend and d[p]==0x0A: p+=1
                    c=0x0A
                elif c==0x5C and p<pend: # backshash!
                    x=d[p]
                    p+=1
                    if   x==0x6E: c=10 # n
                    elif x==0x72: c=13 # r
                    elif x==0x74: c=9  # t
                    elif x==0x62: c=8  # b
                    elif x==0x66: c=12 # f
                    elif x==10 or x==13: # multiple lines: a backslash+sorvege nem resze a stringnek
                        if x==13 and p<pend and d[p]==10: p+=1
                        continue
                    elif x>=0x30 and x<=0x37: # \ddd oktalis kod, 1-3 szamjegy (malware-ek szeretik)
                        c=x-0x30
                        n=1
                        while n<3 and p<pend and 0x30<=d[p]<=0x37:
                            c=c*8+d[p]-0x30
                            p+=1
                            n+=1
                        c&=0xFF
                    else:
                        c=x  # \( \) \\  es ismeretlen escape: a backslash-t eldobjuk
                data.append(c)
            if in_str>0:
                # le nem zart string (pl. Oracle PDF driver: "/CreatorDate (" ): a szabaly szerint a vegeig tartana,
                # es a linearis olvasasnal az egesz file-t lenyelne. Az elso sorvegnel zarjuk le.
                err("Unterminated string at end of data (closed at the end of the line)")
                e=[x for x in (d.find(b'\r',s0,pend),d.find(b'\n',s0,pend)) if x>=0]
                if e:
                    p=min(e)
                    data=bytearray(d[s0:p])
            return p,PDFString(data)

        if c==0x2F:  # /name
            data.append(c) # / jel az elejere!
            while p<pend:
                c=d[p]
#                if c<0x21 or c>0x7E: break
                if c<=0x20 or c in [40,41, 60,62, 91,93, 123,125, 47, 37]: break  #  () <> [] {} / %
                if c==0x23 and p+2<pend:          #    #xx (hex)   pl. /J#53 = /JS
                    x=hexdigit(d[p+1])
                    x2=hexdigit(d[p+2])
                    if x!=None and x2!=None:
                        c=(x<<4)|x2
                        p+=2
                p+=1
                data.append(c)
            return p,bytes(data)

        else:
            # read BODY
            # (az aktualis karaktert eloszor hozzafuzzuk, es utana nezzuk a kovetkezot: igy a buffer legvegen
            # allo token utolso karaktere sem veszik el, pl. objstm utolso obj-a, "true" -> "tru" volt)
            data.append(c)
            while p<pend:
                c=d[p] # next char
                if c<=0x20 or c in [40,41, 60,62, 91,93, 123,125, 47, 37]:  #  () <> [] {} / %  = whitespace/separator
                    break
                p+=1
                data.append(c)
            try:
                if len(data)<=20: return p,int(data)   # (tobb millio jegyu "szamot" nem alakitunk at: negyzetes ideju lenne)
            except Exception:
                pass
            return p,bytes(data)

    return p,None # EOF


# b'/Filter', '[', b'/ASCII85Decode', b'/FlateDecode', ']
def objs_value(objs,name):
  try:
    i=objs.index(name)
    i+=1
    d=objs[i]
    if d=='[':
        d=[]
        i+=1
        while objs[i]!=']':
            d.append(objs[i])
            i+=1
        return d
    else:
        return [d]
  except Exception:
    return None

# az objs[i]-nel kezdodo '<' ... '>' dict feldolgozasa (csak a legfelso szint):  {b'/Name': ertek, ...}
# a beagyazott dict/tomb erteke None, a referencia ('R',oid)
def objs_dict(objs,i):
    dct={}
    key=None
    depth=0
    while i<len(objs):
        t=objs[i]
        i+=1
        if t=='<' or t=='[':
            depth+=1
            if depth==2 and key!=None:
                dct[key]=None
                key=None
            continue
        if t=='>' or t==']':
            depth-=1
            if depth<=0: break
            continue
        if depth!=1: continue
        if key==None:
            if type(t)==bytes and t.startswith(b'/'): key=t
        else:
            if type(t)==int and i+1<len(objs) and type(objs[i])==int and objs[i+1]==b'R':
                t=('R',t)
                i+=2
            dct[key]=t
            key=None
    return dct

# /DecodeParms << ... >>   vagy   /DecodeParms [ null << ... >> ]  (filterenkent)
# a predictort tartalmazo dict-et adja vissza (ha van), kulonben az elsot
def objs_decodeparms(objs):
    for name in [b'/DecodeParms',b'/DP']:
        try:
            i=objs.index(name)+1
        except ValueError:
            continue
        if i>=len(objs): return None
        if objs[i]=='<': return objs_dict(objs,i)
        if objs[i]!='[': return None
        dicts=[]
        depth=0
        while i<len(objs):
            t=objs[i]
            if t=='[': depth+=1
            elif t==']':
                depth-=1
                if depth<=0: break
            elif t=='<' and depth==1: dicts.append(objs_dict(objs,i))
            i+=1
        for dct in dicts:
            if b'/Predictor' in dct: return dct
        return dicts[0] if dicts else None
    return None


NULLCHUNKS=[b' null'*4096,b' null'*64,b' null']
re_nullrun=re.compile(rb'(?:[\x00\t\n\x0c\r ]+(?:null|true|false)(?![^\x00\t\n\x0c\r ()<>\[\]{}/%]))+')

# lenref(oid,gen): a hivatkozott /Length obj erteke (int) vagy None (l. PDFParser.resolve_length)
def parse_pdf_obj(d,p,pend,stop=None,err=print_err,lenref=None):
    pend=min(pend,len(d))
    objs=[]
    starts=[]   # a tokenek kezdete (elotte whitespace lehet)
    stream=None
    while p<pend:
        starts.append(p)
        p,data=parse_pdf_param(d,p,pend,err)
        if data==None: break  # EOF
        objs.append(data)
        # uj "N G obj" fejlec a kozepen: az elozo obj-bol hianyzik az endobj, vagy egy xref szakasz utan nincs
        # startxref. Itt lezarjuk, kulonben az utana kovetkezo obj-eket is lenyelnenk.
        if data==b'obj' and len(objs)>3 and type(objs[-2])==int and type(objs[-3])==int:
            return starts[-3],objs[:-3],stream
        if data in (b'null',b'true',b'false'):
            # hosszu "null null null ..." sorozatok (pl. tagged pdf /ParentTree: szazmillio byte is lehet) atugrasa
            # egyben: az elemzeshez nem kellenek, es szintaktikai hiba sem lehet bennuk. A szabalyos " null" ismetlodest
            # nagy darabokban, byte osszehasonlitassal ugorjuk at (gyors), a maradekot regex-szel.
            for chunk in NULLCHUNKS:
                while d.startswith(chunk,p) and p+len(chunk)<=pend: p+=len(chunk)
            m=re_nullrun.match(d,p,pend)
            if m: p=m.end()
            continue
        if data==b'%%EOF': break  # %%EOF
        if data==stop: break      # endobj

        if data==b'startxref':
#            print("Xref: %d bytes left"%(pend-p))
            q,data=parse_pdf_param(d,p,pend,err)
            try:
                xo=int(data)
#                print("Xref: offset=%d !"%(xo))
                objs.append(data)
                return q,objs,stream
            except Exception:
                err("Xref: INVALID offset format")

        # handle embedded image:
        if data in [b'ID',b'BI',b'EI']: print("STREAM: embedded image !!! "+str(objs))
#        if data==b'ID' and b'BI' in objs:

        # handle embedded stream:
        if data==b'stream':
            stream_len=0
            try:
                fi=objs.index(b'/Length')
                stream_len=int(objs[fi+1])
                if fi+3<len(objs) and objs[fi+3]==b'R': stream_len=-stream_len # referenced object
            except Exception:
                pass
            if stream_len<0 and lenref:
                # hivatkozott /Length ("12 0 R"): feloldjuk. Csak akkor hasznaljuk (lent), ha a hossz utan tenyleg
                # endstream all, igy a rossz feloldas nem art. Nelkule az elso 'endstream' szoveg dontene, ami a
                # tomoritetlen csatolmanyt (pl. beagyazott pdf) a benne levo 'endstream'-nel elvagna.
                try:
                    L=lenref(-stream_len,int(objs[fi+2]))
                    if type(L)==int and L>0: stream_len=L
                except Exception:
                    pass
            declared=stream_len if stream_len>0 else None
            filt=objs_value(objs,b'/Filter')
            parms=objs_decodeparms(objs)
#            if filt: filt=b''.join(filt)
            # 0D 0A 65 │ 6E 64 73 74 │ 72 65 61 6D │ 0D 0A
            if p<pend and d[p] in [9,32]: p+=1 # skip whitepace after 'stream'
            if p<pend and d[p] in [13,10]: # skip CR/LF
                if d[p]==13 and p+1<pend and d[p+1]==10: p+=1
                p+=1
            # 'p' points to stream data!
            q=-1
            if stream_len>0:
                # ha a /Length utan (esetleg whitespace-ek utan) ott az endstream, akkor a /Length jo,
                # igy a stream adataban elofordulo 'endstream' szoveg sem zavar
                e=p+stream_len
                while e<pend and e<p+stream_len+8 and d[e] in WHITESPACE: e+=1
                if d[e:e+9]==b'endstream':
                    stream=PDFStream(d[p:p+stream_len],filt,parms)
                    stream.pos,stream.endpos,stream.declared=p,e,declared
                    objs.append(stream)
                    p=e
                    continue
            q=d.find(b'endstream',p,pend)
            if q<0:
                if stream_len>0 and p+stream_len<=pend:
                    err("STREAM: missing endstream! using /Length=%d at 0x%X"%(stream_len,p))
                    stream=PDFStream(d[p:p+stream_len],filt,parms)
                    objs.append(stream)
                    p+=stream_len
                else:
                    err("STREAM: missing endstream! at 0x%X"%(p))
            else:
                # ez nem annyira jo otlet, van olyan file ahol a 0D 0A a vege a streamnek de csak a 0A a newline, a 0D meg adat!
#                if d[q-1] in [13,10]:  # skip CR/LF
#                    q-=1
#                    if d[q]==10 and d[q-1]==13: q-=1
                # 'q' points to end of stream data!
#                print("STREAM DATA  %s  0x%X - 0x%X  %d/%d%s"%(str(b'---' if not filt else b''.join(filt)), p,q,q-p,stream_len,"  !!!" if (q-p)<stream_len else ""))
                if q>p:
                    if stream_len>(q-p):
                        # a /Length utan nincs endstream (azt fent mar neztuk), tehat a /Length rossz:
                        # az endstream-ig tarto resz a stream (kulonben a kovetkezo obj-ek is belekerulnenek)
                        err("STREAM: invalid length / /Length points beyond endstream %d/%d 0x%X-0x%X"%(stream_len,q-p,p,q))
                        stream_len=q-p
                    if stream_len<(q-p)-3:
                        if stream_len>0: err("STREAM: invalid length / too long! %d/%d 0x%X-0x%X"%(stream_len,q-p,p,q))
                        stream_len=q-p  # ha a stream_len nem jo, akkor az endstream poziciojat vesszuk figyelembe!
                    stream=PDFStream(d[p:p+stream_len],filt,parms)
                    stream.pos,stream.endpos,stream.declared=p,q,declared
                    objs.append(stream)
                elif stream_len>0:
                    err("STREAM: missing data (%d) 0x%X"%(stream_len,p))
                p=q

    return p,objs,stream


# az obj eleje:  "12 0 obj"
# (csak szamsor elejen indulhat, es korlatos hosszu: az alairt pdf-ek /Contents <0000...> helyfoglaloja szazezer
# jegyu szamsor, azon a korlatlan \d+ negyzetes ideju lenne)
re_objstart=re.compile(rb'(?<![0-9])(\d{1,10})[\x00\t\n\x0c\r ]{1,32}(\d{1,10})[\x00\t\n\x0c\r ]{1,32}obj')
re_xreftype=re.compile(rb'/Type[\x00\t\n\x0c\r ]*/XRef\b')
# egy csak szamot tartalmazo obj ("12 0 obj 4567 endobj"): a hivatkozott /Length feloldasahoz
re_lenobj=re.compile(rb'\d{1,10}[\x00\t\n\x0c\r ]{1,32}\d{1,10}[\x00\t\n\x0c\r ]{1,32}obj[\x00\t\n\x0c\r ]*(\d{1,10})[\x00\t\n\x0c\r ]*endobj')
# a %PDF header elotti szemet tipusa
def junk_kind(j):
    if j[:3]==b'\xef\xbb\xbf': return 'UTF-8 BOM'
    if j[:4]==b'rtfd': return 'RTFD package (macOS rich text with attachments)'
    if j[:4]==b'PK\x03\x04': return 'ZIP archive'
    if j[:4] in (b'\xd0\xcf\x11\xe0',): return 'OLE2 container (MS Office / msg)'
    if j[:5]==b'{\\rtf': return 'RTF document'
    # regi Mac: 128 byte-os MacBinary fejlec (0, nev hossza, nev, ..., tipus/letrehozo a 65. byte-tol)
    if len(j)==128 and j[0]==0 and 1<=j[1]<=63 and j[74]==0: return 'MacBinary header, type/creator %s'%(j[65:73].decode('latin-1'))
    if j.lstrip()[:1]==b'<': return 'HTML'
    if re.search(rb'(?i)content-type:|content-transfer-encoding:|^--|^------=_',j.lstrip()): return 'MIME headers'
    if all(32<=c<127 or c in (9,10,13) for c in j): return 'text: %s'%(j.strip()[:40].decode('ascii'))
    return 'binary data'

# pdf szerkezet (a %%EOF utani resz csonka pdf-e, vagy csak szemet / html)
re_structure=re.compile(rb'(?<![0-9])\d{1,10}[\x00\t\n\x0c\r ]{1,32}\d{1,10}[\x00\t\n\x0c\r ]{1,32}obj\b|(?<![a-z])xref[\x00\t\n\x0c\r ]+\d{1,10}[\x00\t\n\x0c\r ]+\d{1,10}[\x00\t\n\x0c\r ]|trailer[\x00\t\n\x0c\r ]*<<|endobj|endstream|\d{10} \d{5} [nf]')
re_script=re.compile(rb'(?is)<script\b([^>]*)>(.*?)</script\s*>')
re_html=re.compile(rb'<(!DOCTYPE|html|head|body|script|div|meta|!--)',re.I)
re_complete_tail=re.compile(rb'startxref[\x00\t\n\x0c\r ]+\d+[\x00\t\n\x0c\r ]*(%(%(E(OF?)?)?)?)?[\x00\t\n\x0c\r ]*$')

def top_dict(objs):
    return objs_dict(objs,3) if len(objs)>3 and objs[3]=='<' else {}

# pdf szoveg (PDFDocEncoding / UTF-16 BOM-mal) -> utf-8 byte-ok, olvashato kiirashoz / tartalomhoz
def text_bytes(b):
    if b.startswith(b'\xfe\xff') or b.startswith(b'\xff\xfe'): return b.decode('utf-16','replace').encode('utf-8')
    return b

def decode_filename(s):
    if s.startswith(b'\xfe\xff') or s.startswith(b'\xff\xfe'): return s.decode("utf-16",errors="ignore")
    return s.decode("utf-8",errors="ignore")


class PDFParser():

    def __init__(self,d,debug=False,validate=False):
        self.d=d
        self.debug=debug
        self.validate=validate
        self.errors=[]      # [(suly,uzenet),...]
        self.content=[]     # [(data,filename),...]
        self.encrypt=0
        self.xref={}        # oid -> (offset,gen)          ASCII xref tabla, vagy binaris xref type=1
        self.badxref=set()  # az xref-ben rossz pozicioval szereplo oid-k
        self.xref_stream_oids=set() # az xref streamek obj szamai
        self.xref_zero=set() # 0 offsetu "n" bejegyzesek (hasznalaton kivuli obj szamok, pl. macOS Quartz)
        self._starts=None   # a file-ban talalhato "N G obj" poziciok (obj_starts)
        self.base=0         # az offsetek ehhez kepest ertendok (szemet a header elott)
        self.truncated=False
        self.deep_header=False  # a %PDF header az elso 1024 byte-on tul van
        self.quiet=False    # a hibakat csak kiirjuk, nem szamoljuk (a %%EOF utani regi adat maradekaban)
        self.leftover=None  # a %%EOF utani regi adat kezdete
        self.oid=0          # az utoljara feldolgozott obj
        self.xref_stm={}    # oid -> (objstm oid,index)    binaris xref type=2 (object streamben levo obj)
        self.objstm={}      # objstm oid -> set(a benne levo oid-k)
        self.dom={}         # oid -> (start,end) pozicio a file-ban
        self.strobjs={}     # oid -> string (a csak egy stringet tartalmazo obj-ek, pl. /JS 12 0 R eseten)
        self.fsobjs={}      # oid -> a filespec nevei
        self.launchrefs=[]  # Launch action-ok, amelyeknek az /F-je hivatkozas: (eddigi cel, [oid,...])
        self.jsrefs={}      # /JS altal hivatkozott obj-ek (dict, hogy a sorrend megmaradjon)
        self.pagecnt=0
        self.pagenum=None
        self.objsnum=0
        self.uriobjid=-1
        self.streamname="pdfstream.dat"
        # sorvege-serules (szoveges atvitel, pl. base64 nelkul kuldott email) felismeresehez:
        self.len_ok=0       # a /Length stimmel
        self.len_crlf=0     # hosszabb, legfeljebb annyival, ahany CRLF van benne  (LF -> CRLF)
        self.len_lf=0       # rovidebb, legfeljebb annyival, ahany LF van benne    (CRLF -> LF)
        self.len_other=0    # mas elteres
        self.len_mismatch=[] # (a stream adatanak pozicioja, tenyleges - /Length) az eltero hosszu streameknel
        self.startxref=None # (a startxref erteke, a 'startxref' kulcsszo pozicioja)
        self.binheader=False # van-e binaris komment a header utan (a levelezok ettol binarisnak latjak a file-t)

    def err(self,msg,n=1):
        print(msg)
        if not self.quiet: self.errors.append((n,msg))

    # html (a pdf elott / utan): egeszeben, es a benne levo <script> blokkok JS-kent is
    def add_html(self,h):
        self.add_content(h,"pdfstream.html")
        for m in re_script.finditer(h):
            if m.group(2).strip():
                print("JSCR(html): %d bytes: %s"%(len(m.group(2)),str(m.group(2)[:256])))
                self.add_content(m.group(2),"pdfstream.js")

    def add_content(self,data,name):
        self.content.append((None if self.validate else data,name))

    def parse(self):
      d=self.d
      try:
        pend=len(d)

        p=d.find(b'%PDF-',0,1024)
        if p<0: p=d.find(b'%FDF-',0,1024)
        if p<0:
            # a header az elso 1024 byte-on tul: az Acrobat nem, de mas nezok (pl. a macOS PDFKit) igy is megnyitjak,
            # pl. RTFD csomagba agyazott pdf. A tartalom kinyeres miatt ezt is feldolgozzuk (JUNK hiba).
            p=d.find(b'%PDF-')
            if p<0: return False # "not pdf"
            self.deep_header=True
        hdr=p
        # a header ("%PDF-1.4") vege: innen parsolunk tovabb. A header sor vegeig NEM ugrunk at, mert van
        # ahol az elso obj ugyanabban a sorban kezdodik ("%PDF-1.5 www.opoosoft.com2 0 obj", vagy a binaris
        # komment utan sorvege nelkul: "%\xe1\xfc\xf6\xf34 0 obj"), azt az elso "N G obj" keresese talalja meg.
        p+=5
        while p<pend and d[p] in b'0123456789.': p+=1
        headend=p
        while p<pend and d[p]!=10 and d[p]!=13 and p<1024: p+=1

        q=p
#   2102 newline: b'\n' 1
#   2449 newline: b'\r' 1
#   1355 newline: b'\r\n' 2
#     18 newline: b'\n\n' 2    BAD!!!
#     17 newline: b'\r\n\r\n' 4  BAD!!!
#    while p<pend and (d[p]==10 or d[p]==13) and d[p]!=d[p-1] and p<1024: p+=1
        if p<pend and d[p]==13: p+=1 # \r
        if p<pend and d[p]==10: p+=1 # \n
        newline=d[q:p]
        if self.debug: print("newline:",newline,len(newline))

        q=p
        if p<pend and d[p]==37:    # skip  %comment:
            while p<pend and d[p]!=10 and d[p]!=13 and p<1024: p+=1
#            while p<pend and (d[p]==10 or d[p]==13) and p<1024: p+=1
            if p<pend and d[p]==13: p+=1 # \r
            if p<pend and d[p]==10: p+=1 # \n
        else:
            print("bad pdf header! p=%d"%(p))
        if self.debug and p>q: print("comment:",d[q:p],p-q)
        self.binheader=any(c>=128 for c in d[q:p])
        p=headend

        q=d.rfind(b'startxref',p) # len(d)-4096)
        if q>0:
            oend=q
            q+=9
            while q<pend and d[q]<=32: q+=1 # skip whitespace
            o=q
            while q<pend and not d[q] in [10,13]: q+=1
            if self.debug: print('XREF: offset='+str(d[o:q]))
            try:
                o=int(d[o:q])
            except ValueError:
                # hianyzo / nem szam ertek (pl. "startxref\n%%EOF"): celzott hiba, nem kivetel. A %%EOF ellenorzes
                # es az xref keresese (lent) igy is lefut.
                self.err('XREF: invalid startxref value: %s'%(str(d[o:q][:32])))
                q=o
                o=-1
            try:
                if o>=0: self.startxref=(o,oend)
                while q<pend and d[q]<=32: q+=1 # skip whitespace
                if d[q:q+5]==b'%%EOF':
                    if pend>q+7 and re_structure.search(d,q+5):
                        # az EOF utan meg obj-ek vannak: csonka file (pl. linearizalt, ahol csak az elso oldal
                        # startxref-je maradt meg) -> nem vagjuk le, vegigolvassuk (check_truncated jelzi)
                        print('XREF: %d bytes with objects after EOF'%(pend-(q+5)))
                    else:
                        if pend>q+7 and re_html.search(d,q+5,q+5+4096):
                            # html a pdf utan (webszerver / letolto oldal hozzafuzte). A pdf nezok nem futtatjak, de
                            # bongeszoben a file html-kent is ertelmezheto (poliglott): tartalomkent is kiadjuk.
                            self.err('JUNK: %d bytes after %%%%EOF (HTML)'%(pend-(q+5)))
                            self.add_html(d[q+5:pend])
                        elif pend>q+7 and d[q+5:pend].strip(b'\x00'+WHITESPACE)==b'':
                            print('XREF: %d bytes zero padding after EOF'%(pend-(q+5)))
                        elif pend>q+7:
                            if pend>=q+4096: self.err('XREF: %d bytes left after EOF'%(pend-(q+5)))
                            else: print('XREF: %d bytes left after EOF'%(pend-(q+5)))
                        pend=q+5 # update pend... (shit after EOF, should not be parsed)
                else:
                    self.err('XREF: missing EOF!',5)
                # szemet a %PDF header elott (UTF-8 BOM, HTML, levelezo fejlec...): ilyenkor az offsetek a headerhez
                # relativak (az olvasok is igy kezelik)
                if hdr>0 and d[:hdr].strip(WHITESPACE)==b'':
                    print("JUNK: %d whitespace bytes before the %%PDF header"%(hdr))   # artalmatlan
                    if o>=0 and not self.section_at(o) and self.section_at(o+hdr):
                        self.base=hdr
                        o+=hdr
                elif hdr>0:
                    kind=junk_kind(d[:hdr])
                    if kind=='HTML': self.add_html(d[:hdr])   # (l. az EOF utani html-t)
                    if self.deep_header: kind+=", header beyond the first 1024 bytes"
                    if o>=0 and not self.section_at(o) and self.section_at(o+hdr):
                        self.base=hdr
                        o+=hdr
                        self.err("JUNK: %d bytes before the %%PDF header (%s), offsets are relative to the header"%(hdr,kind))
                    else:
                        print("JUNK: %d bytes before the %%PDF header (%s)"%(hdr,kind))
                # parse it!
                if o<p or o>=oend:
                    # invalid offset, find xref...
                    o2=d.rfind(b'xref',p,oend) # a header vege es a startxref kezdte kozott keresunk visszafele...
                    if o>=0: self.err('XREF: invalid startxref offset %d, xref found at %d'%(o,o2))
                    else: print('XREF: trying xref found at %d'%(o2))   # (a hibat mar szamoltuk)
                    o=o2
                if o>=0: self.parse_xref(o,oend,pend)
            except Exception:
                self.err('XREF: exception!!! '+traceback.format_exc(),10)
        else:
            self.err('XREF: NOT FOUND!!!',10)

        self.verify_xref(headend,pend)
        self.check_xref_zero()
        self.check_truncated()

        # csonka file-nal az xref sem teljes, azt sem jarjuk be
        if self.validate and self.xref and len(self.badxref)*2<=len(self.xref) and not self.truncated:
            # validate modban eleg az xref altal mutatott (ervenyes) obj-eket bejarni: szerkesztett pdf-ben
            # ugyanaz az obj tobbszor is szerepelhet, de csak az utolso ervenyes, az xref arra mutat.
            self.walk_xref(pend)
        else:
            # content kigyujtesnel (vagy ha nincs hasznalhato xref, vagy a fele rossz) vegigolvassuk az egesz file-t,
            # igy az elrejtett, xref-ben nem szereplo obj-eket is megtalaljuk
            self.scan_objs(p,pend)
            self.quiet=False

        self.verify_xref_stm()
        self.resolve_js()
        self.resolve_launch()

      except Exception:
        self.err("PDFparse-Exception!!! %s" % (traceback.format_exc()),10)

      try:
        self.check_transfer()
        if not any(m.startswith('TRANSFER') for w,m in self.errors): self.check_gap()
      except Exception:
        self.err("PDFparse-Exception!!! %s" % (traceback.format_exc()),10)

      if self.debug: print(self.dom)
      print("PDF: %d/%s pages, %d+%d objs"%(self.pagecnt,str(self.pagenum),len(self.dom),self.objsnum))
      return True

    # az xref-ben szereplo obj-ek feldolgozasa, file-beli sorrendben
    def walk_xref(self,pend):
        xref=dict(self.xref)
        for oid in self.badxref:
            # a rossz pozicioju obj-et megkeressuk a file-ban (az utolso elofordulast), ahogy az olvasok is
            # (a hibat mar szamoltuk)
            pos=None
            for m in re.finditer(rb'(?<![0-9])%d[\x00\t\n\x0c\r ]+%d[\x00\t\n\x0c\r ]+obj'%(oid,xref[oid][1] if type(xref[oid][1])==int else 0),self.d):
                pos=m.start()
            if pos==None or pos>=pend: del xref[oid]
            else: xref[oid]=(pos,xref[oid][1])
        noend=[]
        for oid,(pos,gen) in sorted(xref.items(),key=lambda x:x[1][0]):
            p,objs,stream=parse_pdf_obj(self.d,pos,pend,stop=b'endobj',err=self.err,lenref=self.resolve_length)
            if self.debug: print(objs)
            if not objs or objs[-1]!=b'endobj': noend.append(oid)
            self.process_obj(objs,stream,pos,p)
        # (egy iro program tobb obj-nel is kihagyhatja: file-onkent egy hiba)
        if noend: self.err("%d objects without endobj (e.g. #%d)"%(len(noend),noend[0]))

    # a file linearis vegigolvasasa
    def scan_objs(self,p,pend):
        d=self.d
        # ignore garbage before first obj (nem szamit hibanak)
        # nem a kovetkezo sor elejeig ugrunk, hanem az elso "N G obj"-ig, igy a szemettel egy sorban kezdodo obj sem veszik el
        m=re_objstart.search(d,p,max(p,min(pend,1024)))
        if m and m.start()>p:
            g=d[p:m.start()].strip()
            # a szokasos sorvege + binaris komment sor nem szemet
            if g and not (g.startswith(b'%') and not b'\n' in g and not b'\r' in g): print("Ignoring initial garbage:",g)
            p=m.start()

        while p<pend-5:

            while p<pend and d[p]<=0x20: p+=1  # ignore whitespace before obj
            objp=p
            # a %%EOF utani regi adat maradekat is feldolgozzuk (tartalom), de a hibait nem szamoljuk (JUNK)
            self.quiet=self.leftover!=None and objp>=self.leftover

            p,objs,stream=parse_pdf_obj(d,p,pend,stop=b'endobj',err=self.err,lenref=self.resolve_length)
            if not objs: break # EOF
            if self.debug: print(objs)

            # szemet az obj elott? resync az "N G obj"-re
            if len(objs)>=3 and objs[2]!=b'obj' and not objs[0] in [b'xref',b'startxref',b'%%EOF']:
                for k in range(1,len(objs)-2):
                    if objs[k+2]==b'obj' and type(objs[k])==int and type(objs[k+1])==int:
                        self.err("Ignoring garbage before obj: "+str(objs[:k]))
                        objs=objs[k:]
                        break

            self.process_obj(objs,stream,objp,p)

    # egy "N G obj ... endobj" feldolgozasa. objp,p: a kezdete es vege a file-ban
    def process_obj(self,objs,stream,objp,p):
            if len(objs)>=3 and objs[2]==b'obj':
                oid=self.oid
                try:
                    oid=self.oid=int(objs[0])
                    ogen=int(objs[1])
                    self.dom[oid]=(objp,p) #objs
                except Exception:
                    self.err("INVALID object id!")

                if self.encrypt==oid:
                    if self.debug: print("CRYPT: "+str(objs[3:]))
                    print("CRYPT: V="+str(objs_value(objs,b'/V'))+" len="+str(objs_value(objs,b'/Length'))+" cfm="+str(objs_value(objs,b'/CFM')) )

                if stream: self.parse_stream(oid,objs,stream)
                self.analyze_obj(oid,objs)

            elif objs and not objs[0] in [b'xref',b'startxref',b'%%EOF']:
                self.err("INVALID object type: "+str(objs[0]))


    # kezdodik-e xref szekcio (ASCII tabla vagy xref stream obj) az 'o' pozicion?
    def section_at(self,o):
        d=self.d
        if o<0 or o>=len(d): return False
        while o<len(d) and d[o] in WHITESPACE: o+=1
        return d.startswith(b'xref',o) or re_objstart.match(d,o)!=None

    # xref szekciok feldolgozasa (ASCII tabla vagy binaris xref stream), a /Prev lancot is kovetve
    def parse_xref(self,o,oend,pend):
        todo=[(o,oend)]
        seen=set()
        while todo:
            o,end=todo.pop(0)
            if o in seen:
                # tobb uton is el lehet jutni ugyanahhoz a szekciohoz (/XRefStm + /Prev), ez nem hiba
                if self.debug: print("XREF: section at %d already parsed"%(o))
                continue
            seen.add(o)
            q,objs,stream=parse_pdf_obj(self.d,o,end,stop=b'endobj',err=self.err,lenref=self.resolve_length)
            if self.debug: print("XREF: "+str(objs))
            if not objs:
                self.err("XREF: empty xref section at %d"%(o))
                continue
            if not self.encrypt and b'/Encrypt' in objs:
                try:
                    self.encrypt=int(objs_value(objs,b'/Encrypt')[0])
                except Exception:
                    self.encrypt=-1
                if self.debug: print("CRYPT: "+str(self.encrypt))
            if objs[0]==b'xref':
                self.parse_xref_table(objs)
            elif b'/XRef' in objs and stream:
                if len(objs)<3 or objs[2]!=b'obj' or type(objs[0])!=int:
                    self.err("XREF: offset %d does not point to the start of the xref stream obj: %s"%(o,str(objs[:3])))
                else:
                    self.xref_stream_oids.add(objs[0])
                self.parse_xref_stream(objs,stream)
            else:
                self.err("XREF: invalid xref section at %d: %s"%(o,str(objs[:5])))
                if len(seen)==1:
                    # a startxref rossz helyre mutat: keressuk meg az utolso xref-et visszafele
                    o2=self.d.rfind(b'xref',0,oend)
                    if o2>=0 and not o2 in seen:
                        print('XREF: trying xref found at %d'%(o2))
                        todo.append((o2,end))
                continue
            # korabbi (incremental update elotti) xref szekciok:
            for name in [b'/Prev',b'/XRefStm']:
                v=objs_value(objs,name)
                if not v: continue
                if v[0]==0 and name==b'/Prev':
                    print("XREF: /Prev 0 (means no previous section, written by the generator instead of omitting it)")
                    continue
                if type(v[0])!=int or v[0]<0 or v[0]+self.base>=pend:
                    self.err("XREF: invalid %s offset: %s"%(name.decode(),str(v[0])))
                    continue
                todo.append((v[0]+self.base,pend))

    # ASCII xref table!
    # [b'xref', 8, 1,
    #           123239, 0, b'n',
    #           15, 1,
    #           120214, 0, b'n',
    #           17, 13,
    #           120292, 0, b'n',
    #           120460, 0, b'n', 120493, 0, b'n', 120536, 0, b'n', 120645, 0, b'n', 120739, 0, b'n', 120937, 0, b'n', 121185, 0, b'n', 121472, 0, b'n', 121679, 0, b'n', 123128, 0, b'n', 123206, 0, b'n', 123410, 0, b'n',
    # b'trailer', '<', b'/Size', 30, b'/Root', 15, 0, b'R', b'/Info', 16, 0, b'R', b'/ID', '[', bytearray(b'\x9f\xf0\x1e\x89\xfb^r\xed\xbeq{\x8fT\xb8Z('), bytearray(b'\x9f\xf0\x1e\x89\xfb^r\xed\xbeq{\x8fT\xb8Z('), ']', b'/Prev', 119711, '>']
    def parse_xref_table(self,objs):
        i=1
        while i<len(objs) and objs[i]!=b'trailer':
            if i+1>=len(objs) or type(objs[i])!=int or type(objs[i+1])!=int:
                self.err("XREF: error! invalid subsection header: %s"%(str(objs[i:i+2])))
                return
            oid=objs[i]
            objn=objs[i+1]
            if self.debug: print("XREF: parsing oid range %d-%d"%(oid,oid+objn-1))
            i+=2
            for j in range(objn):
                if i+2>=len(objs):
                    self.err("XREF: error! end of buffer before trailer")
                    return
                if objs[i+2]==b'n' and type(objs[i])==int and objs[i]!=0: self.xref.setdefault(oid+j,(objs[i]+self.base,objs[i+1]))
                elif objs[i+2]==b'n' and objs[i]==0: self.xref_zero.add(oid+j)   # "0000000000 00000 n", l. check_xref_zero
                elif objs[i+2]==b'f':
                    if oid==1 and i==3 and objs[i+1]==65535: # first node is free but id #1 instead of #0
                        print("XREF: Warning, bad OID for root node! fixing...")
                        oid=0
                else:
                    self.err("XREF: error! invalid type: i=%d j=%d o: %s"%(i,j,str(objs[i:i+3])))
                i+=3
        if i>=len(objs): self.err("XREF: error! missing trailer")

    # Binary xref table!   /W [1 2 1]  /Index [0 10 ...]  /Size 10
    def parse_xref_stream(self,objs,stream):
        try:
            w=[int(x) for x in objs_value(objs,b'/W')]
            size=int(objs_value(objs,b'/Size')[0])
            index=[int(x) for x in (objs_value(objs,b'/Index') or [0,size])]
            if len(w)!=3 or min(w)<0 or len(index)%2: raise ValueError
        except Exception:
            self.err("XREF: invalid binary xref header: /W=%s /Size=%s /Index=%s"%(str(objs_value(objs,b'/W')),str(objs_value(objs,b'/Size')),str(objs_value(objs,b'/Index'))),10)
            return
        dd=stream.decode(predictor=True)
        if stream.error: self.err("XREF: binary xref decoding error: "+stream.error,10)
        if stream.note: print("XREF: binary xref: "+stream.note)
        if self.debug: print("XREF binary: W=%s Index=%s"%(str(w),str(index)),dd.hex(' '))
        rl=sum(w)
        if rl==0:
            self.err("XREF: binary xref: invalid /W %s"%(str(w)))
            return
        def field(row,a,n,default):
            return int.from_bytes(row[a:a+n],'big') if n else default
        pos=0
        for k in range(0,len(index),2):
            for j in range(index[k+1]):
                row=dd[pos:pos+rl]
                pos+=rl
                if len(row)<rl:
                    self.err("XREF: binary xref table too short (%d bytes, oid #%d)"%(len(dd),index[k]+j))
                    return
                t=field(row,0,w[0],1)
                f2=field(row,w[0],w[1],0)
                f3=field(row,w[0]+w[1],w[2],0)
                if t==1 and f2==0: self.xref_zero.add(index[k]+j)
                elif t==1: self.xref.setdefault(index[k]+j,(f2+self.base,f3))
                elif t==2: self.xref_stm.setdefault(index[k]+j,(f2,f3))
        if pos<len(dd): print("XREF: %d bytes left in binary xref table"%(len(dd)-pos))  # elofordul valid file-okban, olvasast nem zavarja

    # verify xref if available:
    def verify_xref(self,pstart,pend):
        d=self.d
        if self.debug and self.xref: print("XREF dom:", self.xref)
        bad=[]
        for oid in self.xref:
          try:
            pos,gen=self.xref[oid]
            if pos<pstart or pos+5>=pend:
                bad.append((oid,pos,"not in range %d - %d"%(pstart,pend)))
                continue
            e=pos
            while e<pend and e<pos+4 and d[e] in WHITESPACE: e+=1
            m=re_objstart.match(d,e)
            if m and int(m.group(1))==oid and int(m.group(2))==gen: continue   # OK!
            bad.append((oid,pos,"data: %s"%(str(d[pos:pos+16]))))
          except Exception:
            bad.append((oid,self.xref[oid],"invalid entry"))
        for oid,pos,why in bad: self.badxref.add(oid)
        if bad: self.report_badxref(bad)

    # a rossz xref bejegyzeseket nem egyenkent szamoljuk (egy iro program hibaja szazakat is okozhat), hanem
    # osszesitve, es ha lehet, a jelleguket is megnevezzuk
    def report_badxref(self,bad):
        d=self.d
        for i,(oid,pos,why) in enumerate(bad):
            if i<5 or self.debug: print("XREF: oid #%s pos=%s BAD! %s"%(str(oid),str(pos),why))
        if len(bad)>5 and not self.debug: print("XREF: ... (%d more)"%(len(bad)-5))
        n=len(bad)
        if n==1:
            oid,pos,why=bad[0]
            self.err("XREF: oid #%s pos=%s BAD! %s"%(str(oid),str(pos),why))
            return
        poss=[pos for oid,pos,why in bad]
        if n>=3 and len(set(poss))==1:
            self.err("XREF: bogus xref table: all %d entries point to offset %s"%(n,str(poss[0])),2)
            return
        other=before=0
        kmax=0
        for oid,pos,why in bad:
            if type(pos)!=int or pos<0 or pos>=len(d): continue
            m=re_objstart.search(d,pos,pos+80)
            if not m: continue
            if int(m.group(1))==oid:
                before+=1
                kmax=max(kmax,m.start()-pos)
            elif m.start()-pos<=4: other+=1
        if n>=3 and other>=n*0.6: self.err("XREF: %d/%d entries point to other objects (misnumbered xref table)"%(n,len(self.xref)),2)
        elif n>=3 and before>=n*0.8: self.err("XREF: %d/%d entries point up to %d bytes before the object"%(n,len(self.xref),kmax),1)
        else: self.err("XREF: %d/%d entries point to wrong positions"%(n,len(self.xref)),2)

    # a binaris xref szerint object streamben levo obj-ek tenyleg ott vannak-e?
    def verify_xref_stm(self):
        if self.encrypt: return  # titkositott file-nal az objstm-eket nem bontjuk ki
        missing={}
        for oid,(stm,idx) in self.xref_stm.items():
            if stm in self.objstm and oid in self.objstm[stm]: continue
            missing[stm]=missing.get(stm,0)+1
        for stm,n in missing.items():
            if stm in self.objstm: self.err("XREF: %d objects not found in object stream #%d"%(n,stm))
            else: self.err("XREF: object stream #%d not found (%d objects)"%(stm,n))

    def parse_stream(self,oid,objs,stream):
        self.check_stream_length(stream)
        top=top_dict(objs)
        is_file=top.get(b'/Type')==b'/EmbeddedFile'
        # minden streamet kitomoritunk (tomoritesi hibak keresese). titkositott file-nal ennek nincs ertelme,
        # ott csak a csatolmanyt vesszuk ki (ahogy van), es a dekodolasi hibakat nem szamoljuk.
        if self.encrypt and not is_file: return
        if top.get(b'/Type')==b'/XRef': return  # ezt a parse_xref mar kibontotta es ellenorizte
        is_objstm=top.get(b'/Type')==b'/ObjStm'
        # titkositott file-nal a csatolmanyt nyersen adjuk vissza (ugysem tudjuk kibontani)
        dd=stream.data if self.encrypt else stream.decode(predictor=is_objstm or is_file)
        if stream.error and not self.encrypt:
            self.err("STREAM: decoding error in obj #%d: %s"%(oid,stream.error))
        if stream.note and not self.encrypt:
            print("STREAM: obj #%d: %s"%(oid,stream.note))

        if is_file:
            print("FILESTREAM.size=%d/%s"%(len(dd),str(top.get(b'/Length'))))
            self.add_content(dd,self.streamname)
            self.streamname="pdfstream.dat"

        if is_objstm and not self.encrypt: # ebben lehet /URI, /JS elrejtve...
            self.parse_objstm(oid,top,dd)

    # adatvesztes (vagy beszurt adat) egy stream belsejeben: az utana kovetkezo osszes xref bejegyzes ugyanannyival
    # csuszik el, es a stream hossza pont ennyivel ter el a /Length-tol (pl. hibas masolas, felbeszakadt es rosszul
    # folytatott letoltes). A hiany helye es merete ismert.
    def check_gap(self):
        if len(self.badxref)<3 or not self.len_mismatch: return
        starts=self.obj_starts()
        rows=[]
        for oid in self.badxref:
            pos,gen=self.xref[oid]
            if type(pos)!=int or type(gen)!=int or not (oid,gen) in starts: return
            P=min(starts[(oid,gen)],key=lambda x:abs(x-pos))
            rows.append((pos,P-pos))
        deltas=set(dl for pos,dl in rows)
        if len(deltas)!=1: return
        dl=deltas.pop()
        first=min(pos for pos,x in rows)
        for spos,diff in self.len_mismatch:
            if spos<first and abs(diff-dl)<=2 and dl!=0:
                msg="GAP: %d bytes %s inside the stream at offset %d (damaged copy?), the rest of the file is shifted (%d xref entries)"%(abs(diff),"missing" if diff<0 else "inserted",spos,len(rows))
                print(msg)
                self.errors.insert(0,(10,msg))
                return

    # a stream tenyleges hossza vs. a /Length: a sorvege-konverzio a streamek hosszat pont a
    # beszurt/torolt CR-ek szamaval valtoztatja meg
    def check_stream_length(self,stream):
        if not stream.declared or stream.endpos==None: return
        region=self.d[stream.pos:stream.endpos]
        L=stream.declared
        # a /Length utan csak whitespace van az endstream-ig: stimmel
        # (az adat maga is vegzodhet sorvegere, es az endstream elotti sorvege el is maradhat)
        if len(region)>=L and len(region)-L<=8 and all(c in WHITESPACE for c in region[L:]):
            self.len_ok+=1
            return
        # az endstream elotti sorvege nem resze az adatnak
        if region.endswith(b'\r\n'): region=region[:-2]
        elif region.endswith(b'\n') or region.endswith(b'\r'): region=region[:-1]
        diff=len(region)-stream.declared
        if diff: self.len_mismatch.append((stream.pos,diff))
        if diff==0: self.len_ok+=1
        elif diff>0 and diff<=region.count(b'\r\n'): self.len_crlf+=1
        elif diff<0 and -diff<=region.count(b'\n'): self.len_lf+=1
        else: self.len_other+=1

    # hol van valojaban az utolso xref szekcio (ASCII 'xref' vagy /Type /XRef obj), a startxref elott?
    def find_last_xref(self,oend):
        d=self.d
        x=d.rfind(b'xref',0,oend)
        start=max(0,oend-65536)
        m=None
        for m in re_xreftype.finditer(d,start,oend): pass
        if m:
            o=None
            for o in re_objstart.finditer(d,max(0,m.start()-4096),m.start()): pass
            if o: x=max(x,o.start())
        return x

    # LF -> CRLF (vagy CRLF -> LF) serules felismerese: a file-t szoveges modban vittek at
    # (tipikusan base64 nelkul kuldott email, vagy ASCII modu FTP), igy a sorvegek -- a binaris
    # streamekben levo 0A byte-okkal egyutt -- megvaltoztak. Jelek:
    #  - a streamek hossza a /Length-hez kepest pont annyival nagyobb, ahany CRLF van bennuk (legfeljebb)
    #  - a startxref annyival mutat a tenyleges xref ele, ahany CRLF van elotte (legfeljebb)
    #  - az xref bejegyzesek ugyanigy csusznak el (a file-ban elorehaladva monoton novekvo mertekben)
    # A visszaalakitas nem megbizhato (a binaris adatban eredetileg is lehet 0D 0A), ezert csak jelezzuk.
    # a file-ban talalhato obj fejlecek: (oid,gen) -> [poziciok]
    # a hivatkozott /Length ("12 0 R") erteke: a fajlban levo "12 0 obj <szam> endobj" (az utolso elofordulas,
    # incremental update-nel az ervenyes; a rossz ertek nem art, mert csak endstream-mel megerositve hasznaljuk)
    def resolve_length(self,oid,gen):
        for pos in reversed(self.obj_starts().get((oid,gen),[])):
            m=re_lenobj.match(self.d,pos)
            if m: return int(m.group(1))
        return None

    def obj_starts(self):
        if self._starts==None:
            self._starts={}
            for mo in re_objstart.finditer(self.d):
                self._starts.setdefault((int(mo.group(1)),int(mo.group(2))),[]).append(mo.start())
        return self._starts

    # "0000000000 00000 n" bejegyzesek: sok iro program (foleg a macOS Quartz) a hasznalaton kivuli obj szamokat
    # "f" helyett igy irja be. A nem letezo obj-re mutato hivatkozas null (spec), az olvasok is igy kezelik,
    # ezert ez nem hiba. (2026-09: a tesztkorpuszban 466 ilyen file, egyikben sem letezett a 0-s obj,
    # es mind megnyilt PDFKit-tel.) Hiba csak akkor, ha az obj megis letezik: akkor tenyleg elveszett az offsetje.
    def check_xref_zero(self):
        # (az xref stream a sajat bejegyzesebe gyakran 0-t ir: a startxref ugyis megtalalja, nem hiba)
        zero=[oid for oid in self.xref_zero if not oid in self.xref and not oid in self.xref_stream_oids]
        if not zero: return
        oids=set(k[0] for k in self.obj_starts())
        found=sorted(oid for oid in zero if oid in oids)
        if found and len(found)==len(zero) and not self.xref and len(zero)>=3:
            self.err("XREF: bogus xref table: all %d entries are 0, the objects exist"%(len(zero)))
        elif found: self.err("XREF: %d/%d entries with offset 0 for existing objects (e.g. #%d)"%(len(found),len(zero),found[0]))
        else: print("XREF: %d unused object numbers marked in use with offset 0 (e.g. macOS Quartz)"%(len(zero)))

    # az xref bejegyzesek elcsuszasa sorvege-konverziora utal-e? return: +1 (LF->CRLF), -1 (CRLF->LF), 0 (nem), bejegyzesek szama
    def check_xref_shift(self):
        if len(self.badxref)<3: return 0,0
        d=self.d
        starts=self.obj_starts()
        rows=[]
        for oid in self.badxref:
            pos,gen=self.xref[oid]
            if type(pos)!=int or type(gen)!=int or not (oid,gen) in starts: continue
            P=min(starts[(oid,gen)],key=lambda x:abs(x-pos))
            rows.append((pos,P-pos,P))
        rows.sort()
        if len(rows)<3: return 0,0
        deltas=[x[1] for x in rows]
        if len(set(deltas))<2: return 0,0   # allando eltolas: nem sorvege (pl. szemet a header elott)
        if all(x>0 for x in deltas) and deltas==sorted(deltas) and all(dl<=d.count(b'\r\n',0,P) for pos,dl,P in rows): return 1,len(rows)
        if all(x<0 for x in deltas) and deltas==sorted(deltas,reverse=True) and all(-dl<=d.count(b'\n',0,P) for pos,dl,P in rows): return -1,len(rows)
        return 0,0

    def check_transfer(self):
        n,m,o=self.len_crlf,self.len_lf,self.len_other
        if n==0 and m==0 and len(self.badxref)<3: return
        d=self.d
        xdelta=0
        xsign=0    # a startxref elcsuszasanak iranya: +1 elore (LF->CRLF), -1 hatra (CRLF->LF)
        xok=None   # a startxref elcsuszasa lehet-e sorvege-konverzio (legfeljebb annyi, ahany sorvege elotte van)
        if self.startxref:
            so,oend=self.startxref
            x=self.find_last_xref(oend)
            if x>=0 and x!=so:
                xdelta=x-so
                xsign=1 if xdelta>0 else -1
                if xdelta>0: xok= xdelta<=d.count(b'\r\n',0,x)
                else: xok= -xdelta<=d.count(b'\n',0,x)
        if self.debug: print("TRANSFER: streams ok=%d crlf=%d lf=%d other=%d  startxref delta=%d %s"%(self.len_ok,n,m,o,xdelta,str(xok)))
        xs,xn=self.check_xref_shift()
        if self.debug: print("TRANSFER: xref entries shift=%d (%d entries)"%(xs,xn))
        # tobb stream egyertelmuen erre utal, vagy egy stream + a startxref is, vagy az xref bejegyzesek
        if n>m and (n>=2 and n>=3*(o+m) or n>=1 and o+m==0 and xok) or xs>0 and m==0:
            kind="LF -> CRLF"
            sign=1
            cnt=n
        elif m>n and (m>=2 and m>=3*(o+n) or m>=1 and o+n==0 and xok) or xs<0 and n==0:
            kind="CRLF -> LF"
            sign=-1
            cnt=m
        else:
            return
        if xs==-sign: return                            # az xref bejegyzesek ellentmondanak
        if xsign and (xsign!=sign or not xok): return   # a startxref ellentmond
        ev=[]
        if cnt: ev.append("%d/%d streams changed by the number of line endings"%(cnt,n+m+o+self.len_ok))
        if xn: ev.append("%d xref entries shifted"%(xn))
        msg="TRANSFER: %s line ending conversion damage (text mode transfer, e.g. email without base64 / ASCII FTP): %s"%(kind,", ".join(ev))
        if xok: msg+=", startxref off by %d"%(xdelta)
        msg+=", binary header comment: %s"%("yes" if self.binheader else "NO")
        print(msg)
        self.errors.insert(0,(10,msg))

    # csonka file (felbeszakadt letoltes / masolas): a vege nullakkal van kitoltve, vagy az utolso %%EOF utan
    # meg obj-ek vannak (pl. linearizalt file-nal csak az elso oldal szekcioja teljes)
    def check_truncated(self):
        d=self.d
        why=[]
        stripped=d.rstrip(b'\x00')
        z=len(d)-len(stripped)
        last=stripped.rfind(b'%%EOF')
        # a file a zaro "startxref N" utan er veget, csak a %%EOF (vagy egy resze) hianyzik: nem csonka
        # (ezt a 'missing EOF' hiba jelzi)
        if re_complete_tail.search(stripped[-64:]): return
        # az utolso %%EOF utan pdf szerkezet (obj, xref, trailer...) kovetkezik: felbeszakadt a file
        # (a %%EOF utani kis szemet / nulla padding viszont nem az)
        after=last>=0 and re_structure.search(stripped,last+5)!=None
        # az EOF utan pdf toredek all, de az EOF elotti dokumentum teljes (az xref minden bejegyzese jo, nincs xref
        # hiba): ez egy regi / masik pdf maradeka (pl. nagyobb file-t felulirtak a csonkolasa nelkul, vagy a file
        # merete blokkmeretre kerekedett), nem csonka file. (2026-09: 50 ilyen a 2. lemezen, mind megnyilt PDFKit-tel.)
        # (ha viszont az EOF utan obj hataron kezdodik egy uj resz, az egy felbeszakadt incremental update: csonka)
        t=stripped[last+5:].lstrip(WHITESPACE) if last>=0 else b''
        boundary=re_objstart.match(t)!=None or t.startswith(b'xref')
        if after and not boundary and self.xref and not self.badxref and not any(m.startswith('XREF') for w,m in self.errors):
            msg="JUNK: %d bytes of leftover PDF data after %%%%EOF (older / other file's data%s)"%(len(stripped)-last-5,", file size is a multiple of 512" if len(d)%512==0 else "")
            print(msg)
            self.errors.insert(0,(1,msg))
            self.leftover=last+5
            return
        if z>=64 and (last<0 or after): why.append("%d zero bytes at the end"%(z))
        if after: why.append("%d bytes with PDF structure after the last %%%%EOF"%(len(stripped)-last-5))
        elif last<0: why.append("no %%EOF")
        if not why: return
        self.truncated=True
        if b'/Linearized' in d[:2048]: why.append("linearized, only the first page section is complete")
        msg="TRUNCATED: incomplete file (e.g. interrupted download or copy): "+", ".join(why)
        print(msg)
        self.errors.insert(0,(10,msg))

    # object stream: "oid1 offs1 oid2 offs2 ... " fejlec (/First byte hosszu), utana az obj-ek
    def parse_objstm(self,oid,top,dd):
        offs=top.get(b'/First')
        onum=top.get(b'/N')
        if type(offs)!=int or type(onum)!=int or offs<0 or offs>len(dd):
            self.err("OBJSTREAM #%d: invalid /First=%s /N=%s"%(oid,str(offs),str(onum)))
            offs=0
            onum=0
        if self.debug: print("OBJSTREAM.offset=%d num=%d data:"%(offs,onum),dd[:offs])
        self.objsnum+=onum
        hdr=dd[:offs].split()
        try:
            pairs=[(int(hdr[k]),int(hdr[k+1])) for k in range(0,len(hdr)-1,2)]
        except ValueError:
            pairs=[]
        if len(hdr)%2 or len(pairs)!=onum:
            self.err("OBJSTREAM #%d: invalid header (%d/%d objs)"%(oid,len(pairs),onum))
        self.objstm[oid]=set(x for x,y in pairs)
        if not pairs:
            # fejlec nelkul: az egeszet egyben vizsgaljuk
            pp,oo,ss=parse_pdf_obj(dd,offs,len(dd),err=self.err)
            if self.debug: print("OBJSTREAM->"+str(oo))
            self.analyze_obj(oid,[oid,0,b'obj']+oo)
            return
        for k,(ooid,ooff) in enumerate(pairs):
            s=offs+ooff
            e=offs+pairs[k+1][1] if k+1<len(pairs) else len(dd)
            if s>len(dd) or e<s:
                self.err("OBJSTREAM #%d: invalid offset for obj #%d: %d"%(oid,ooid,ooff))
                continue
            pp,oo,ss=parse_pdf_obj(dd,s,e,err=self.err)
            if self.debug: print("OBJSTREAM #%d -> #%d: %s"%(oid,ooid,str(oo)))
            self.analyze_obj(ooid,[ooid,0,b'obj']+oo)

    # egy obj vizsgalata: URI, JS, csatolmany neve, oldalak
    def analyze_obj(self,oid,objs):
        top=top_dict(objs)

#        if oid==uriobjid and type(objs[3])==PDFString:
        if len(objs)>3 and type(objs[3])==PDFString:
            uri=objs[3].get()
            self.strobjs[oid]=uri
            if oid==self.uriobjid or b'script' in uri or b'http' in uri:
                print("OBJSTREAM.URI.obj="+str(uri))

        # [29, 0, b'obj', '<', b'/Type', b'/Action', b'/S', b'/URI', b'/URI', 30, 0, b'R', '>', b'endobj']
        # find /URI in object:
        i=0
        while not self.encrypt:
            try:
                i=objs.index(b'/URI',i)
                i+=1
#                print(type(objs[i]))
#                print(objs[i])
                if type(objs[i])==PDFString: print("OBJSTREAM.URI="+str(objs[i].get()))
                if type(objs[i])==int: self.uriobjid=objs[i]
            except Exception:
                break

        # filespec obj-ek nevei (a Launch action /F hivatkozasainak feloldasahoz, objstm-ben levoknel is)
        if top.get(b'/Type')==b'/Filespec' or b'/UF' in top:
            self.fsobjs[oid]=[text_bytes(v.get()) for k,v in top.items() if k in (b'/F',b'/UF',b'/DOS',b'/Unix',b'/Mac') and type(v)==PDFString]

        # find FIle attachment in object:
        if top.get(b'/Type')==b'/Filespec' and not self.encrypt:
            fn=top.get(b'/UF')
            if type(fn)!=PDFString: fn=top.get(b'/F')
            if type(fn)==PDFString:
                print("FILESTREAM.name="+str(fn.get()))
                self.streamname=decode_filename(fn.get())
                if len(self.content)>0 and self.content[-1][1]=="pdfstream.dat":
                    self.content[-1]=(self.content[-1][0],self.streamname) # hu de gany
                    self.streamname="pdfstream.dat"

        # find Javascript (minden elofordulast, egy obj-ben tobb action is lehet)
        i=0
        while True:
            try:
                i=objs.index(b'/JS',i)+1
            except ValueError:
                break
            if i>=len(objs): break
            js=objs[i]
            if type(js)==int and i+2<len(objs) and objs[i+2]==b'R':
                print("JSCR: -> obj #%d"%(js))
                self.jsrefs[js]=True  # a hivatkozott obj-et a vegen keressuk meg
                continue
            if type(js)==PDFString: js=js.get()
            print("JSCR: "+str(js))
            if type(js)==bytes: self.add_content(js,"pdfstream.js")

        # Launch action (program / file inditasa): a celjat (/F file, /Win /F /P parameterek...) kinyerjuk, mint a JS-t.
        # Az egesz action dict-et nezzuk (az /F a /S /Launch elott is allhat), a hivatkozott /F-et a vegen oldjuk fel.
        i=0
        while True:
            try:
                i=objs.index(b'/Launch',i)+1
            except ValueError:
                break
            if objs[i-2]!=b'/S': continue
            start=i-2
            depth=0
            while start>0:
                start-=1
                t=objs[start]
                if t=='>' or t==']': depth+=1
                elif t=='<' or t=='[':
                    if depth==0: break
                    depth-=1
            parts=[]
            refs=[]
            depth=0
            k=start+1
            while k<len(objs):
                t=objs[k]
                if t=='<' or t=='[': depth+=1
                elif t=='>' or t==']':
                    if depth==0: break
                    depth-=1
                elif type(t)==PDFString: parts.append(text_bytes(t.get()))
                elif t in (b'/Win',b'/Unix',b'/Mac',b'/F',b'/P',b'/D',b'/O'): parts.append(t)
                elif type(t)==int and k+2<len(objs) and type(objs[k+1])==int and objs[k+2]==b'R':
                    if objs[k-1]==b'/F': refs.append(t)
                    k+=2
                k+=1
            target=b' '.join(parts)
            if refs: self.launchrefs.append((target,refs))
            else:
                print("LAUNCH: "+str(target))
                self.add_content(target,"pdfstream.launch")

        if top.get(b'/Type')==b'/Page': self.pagecnt+=1
        if top.get(b'/Type')==b'/Pages' and type(top.get(b'/Count'))==int:
            if self.pagenum==None or top[b'/Count']>self.pagenum: self.pagenum=top[b'/Count']  # a gyoker /Pages-ben van a teljes szam

    # a Launch action-ok hivatkozott /F filespec-jei: a benne levo nevek (/F /UF ...)
    def resolve_launch(self):
        for target,refs in self.launchrefs:
            parts=[target] if target else []
            for oid in refs:
                if oid in self.fsobjs: parts+=self.fsobjs[oid]
                elif oid in self.strobjs: parts.append(text_bytes(self.strobjs[oid]))
                elif oid in self.dom:
                    objp,end=self.dom[oid]
                    q,objs,stream=parse_pdf_obj(self.d,objp,end,stop=b'endobj',lenref=self.resolve_length)
                    parts+=[text_bytes(t.get()) for t in objs if type(t)==PDFString]
                else: parts.append(b'(obj #%d not found)'%(oid))
            t=b' '.join(parts)
            print("LAUNCH: "+str(t))
            self.add_content(t,"pdfstream.launch")

    # a /JS 12 0 R altal hivatkozott obj-ek (stream vagy string) kinyerese
    def resolve_js(self):
        for oid in self.jsrefs:
            if oid in self.strobjs:
                js=self.strobjs[oid]
            elif oid in self.dom:
                objp,end=self.dom[oid]
                q,objs,stream=parse_pdf_obj(self.d,objp,end,stop=b'endobj',lenref=self.resolve_length)
                if stream:
                    js=bytes(stream.decode(predictor=True))  # a dekodolasi hibat mar szamoltuk
                else:
                    print("JSCR: obj #%d is not a string or stream: %s"%(oid,str(objs[:8])))
                    continue
            else:
                self.err("JSCR: referenced obj #%d not found"%(oid))
                continue
            print("JSCR(#%d): %d bytes: %s"%(oid,len(js),str(js[:256])))
            self.add_content(js,"pdfstream.js")


# return: (content,errcnt,errors)   nem pdf eseten (None,99,[(99,"not pdf")])
def parse_pdf(d,debug=False,validate=False):
    pdf=PDFParser(d,debug,validate)
    if not pdf.parse(): return None,99,[(99,"not pdf")]
    return pdf.content,sum(n for n,msg in pdf.errors),pdf.errors


# hasznalat: pdfparse.py [-d] [-v] file|konyvtar ...
#   -d  debug
#   -v  validate: csak ellenorzes, a kibontott adatot nem taroljuk
if __name__ == '__main__':
  debug=False
  validate=False
  fl=[]
  for path in sys.argv[1:]:
    if path=="-d":
      debug=True
    elif path=="-v":
      validate=True
    elif os.path.isfile(path):
      fl.append(path)
    else:
      for n in os.listdir(path): fl.append(path+"/"+n)

  for fn in fl:
    try:
      with open(fn,"rb") as f:
        print("=================== %s ====================="%(fn))
        cont,ret,errs=parse_pdf(f.read(),debug,validate)
        for data,name in cont or []:
          print("CONTENT: %s %s bytes"%(name,"?" if data==None else str(len(data))))
        print("ERRORS=%d"%(ret))
#        if ret: os.rename(fn,"hibas/"+fn.split("/")[-1])
    except Exception:
      print("Exception!!! %s" % (traceback.format_exc()))
