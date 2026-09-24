# pdfparse3.py – működési dokumentáció

*Készült: 2026-09-24, a `pdfparse3.py` (1610 sor, Python 3) átnézése alapján. A forrás nem módosult.*

## 1. Cél és áttekintés

A `pdfparse3.py` egy **megengedő PDF-elemző**, amelynek két feladata van:

1. **Tartalom kinyerése** a PDF-ből: beágyazott JavaScript, csatolmányok (`/EmbeddedFile`), Launch action célok, valamint a PDF elé/után fűzött HTML.
2. **Konzisztencia-ellenőrzés**: a szintaktikai, tömörítési és kereszthivatkozási (xref) hibák felderítése és **súlyozott számlálása**.

A program nem PDF-megjelenítő: nem értelmezi az oldalak tartalmát, fontokat, képeket. Ami érdekli, az a fájl szerkezete és a benne rejtett aktív tartalom. Az alapelv: *amit csak tud, feldolgoz; a hibákat nem kivételként dobja, hanem megszámolja*. Ezért a hibás, csonka, sérült fájlokon is végigfut, és lehetőleg egyetlen, jól megnevezett hibát ad egy jelenségre (nem százat egy rossz xref-tábla minden sorára).

A kód a korábbi `pdfparse2.py` és `testpdf.py` összevonása. A megjegyzések magyar nyelvűek, ékezet nélkül.

## 2. Használat

### 2.1 Könyvtárként

```python
from pdfparse3 import parse_pdf
content, errcnt, errors = parse_pdf(data, debug=False, validate=False)
```

| Név | Típus | Jelentés |
|---|---|---|
| `data` | `bytes` | a teljes fájl tartalma |
| `debug` | bool | részletes kiírás (tokenek, xref-tábla, DOM) |
| `validate` | bool | csak ellenőrzés: a kinyert adat helyett `None` kerül a `content`-be, és lehetőség szerint csak az xref által mutatott objektumokat járja be |
| `content` | `[(data, filename), ...]` | a kinyert tartalmak; `filename` a 2.3 szerinti név |
| `errcnt` | int | a hibák súlyainak összege |
| `errors` | `[(súly, üzenet), ...]` | az egyes hibák |

Ha a fájl nem PDF (nincs benne `%PDF-` vagy `%FDF-` fejléc): `(None, 99, [(99, "not pdf")])`.

### 2.2 Parancssorból

```
pdfparse3.py [-d] [-v] fájl|könyvtár ...
```

Könyvtár megadásakor annak minden bejegyzését feldolgozza (nem rekurzív). Kimenete fájlonként:

```
=================== <fájl> =====================
... diagnosztikai sorok (JSCR:, FILESTREAM., XREF:, STREAM:, ...) ...
PDF: <talált /Page objektumok>/<gyökér /Pages /Count> pages, <obj-ek>+<objstm-beli obj-ek> objs
CONTENT: <név> <méret> bytes
ERRORS=<errcnt>
```

A program a futása közben **sok mindent kiír a stdout-ra** (a `print` hívások), függetlenül a `debug` kapcsolótól. A hibának számító üzenetek ezen felül az `errors` listába is bekerülnek (`PDFParser.err`). Ami csak `print`, az megjegyzés/megfigyelés, nem hiba.

### 2.3 A kinyert tartalmak nevei

| Név | Tartalom |
|---|---|
| `pdfstream.js` | JavaScript: `/JS` string vagy stream, illetve HTML `<script>` blokk |
| `pdfstream.dat` vagy a csatolmány fájlneve | `/EmbeddedFile` stream kibontott adata (titkosított PDF-nél nyersen) |
| `pdfstream.html` | a `%PDF` fejléc előtti vagy a `%%EOF` utáni HTML |
| `pdfstream.launch` | Launch action célja: a `/F`, `/Win`, `/Unix`, `/Mac`, `/P`, `/D`, `/O` értékek szóközzel összefűzve |

### 2.4 Hibasúlyok

| Súly | Mikor |
|---|---|
| 1 | alapértelmezett (szintaktikai hiba, rossz stream-hossz, dekódolási hiba, egyedi xref-hiba, JUNK a fejléc előtt, `%%EOF` utáni régi PDF-maradék stb.) |
| 2 | tömegesen rossz xref-tábla (minden bejegyzés egy offsetre mutat, elszámozott tábla, rossz pozíciók) |
| 5 | hiányzó `%%EOF` |
| 10 | súlyos szerkezeti hiba: nincs `startxref`, kivétel az xref feldolgozásában, érvénytelen/dekódolhatatlan bináris xref, `TRUNCATED`, `TRANSFER`, `GAP`, belső kivétel (`PDFparse-Exception`) |
| 99 | nem PDF |

A `TRANSFER`, `GAP`, `TRUNCATED` és a `%%EOF` utáni maradék (`JUNK ... leftover`) üzenetek a lista **elejére** kerülnek (`errors.insert(0, ...)`), mert ezek magyarázzák a többi hibát.

## 3. A feldolgozás menete (`PDFParser.parse`, 796. sor)

```
fejléc keresése (%PDF- / %FDF-, első 1024 byte, majd bárhol)
  └─ a fejléc-sor és a bináris komment átugrása, binheader megjegyzése
startxref megkeresése a fájl végéről (rfind)
  ├─ offset beolvasása, %%EOF ellenőrzése
  ├─ a %%EOF utáni rész osztályozása: obj-ek (csonka), HTML, nulla-padding, szemét → pend levágása
  ├─ a fejléc előtti szemét: ha az offsetek a fejléchez relatívak, base=hdr
  └─ parse_xref: xref-szekciók (ASCII tábla / xref stream) a /Prev és /XRefStm láncon
verify_xref       – minden xref-bejegyzés tényleg a "N G obj"-ra mutat-e (badxref)
check_xref_zero   – "0 offsetű n" bejegyzések: létező objektumok-e
check_truncated   – csonka fájl, ill. régi PDF-maradék a %%EOF után
objektumok bejárása
  ├─ validate módban, jó xref-fel: walk_xref (csak az xref által mutatott obj-ek, fájlbeli sorrendben)
  └─ egyébként: scan_objs (lineáris végigolvasás, a rejtett obj-eket is megtalálja)
       └─ process_obj → parse_stream (dekódolás, objstm bontás) + analyze_obj (JS, URI, Launch, Filespec, oldalak)
verify_xref_stm   – a bináris xref szerinti objstm-tagság ellenőrzése
resolve_js        – "/JS 12 0 R" hivatkozások feloldása
resolve_launch    – Launch /F hivatkozások feloldása
check_transfer    – sorvége-konverzió (LF↔CRLF) felismerése
check_gap         – hiányzó/beszúrt byte-ok egy stream belsejében (ha nincs TRANSFER)
```

A `parse()` két nagy `try` blokkban fut: bármilyen váratlan kivétel `PDFparse-Exception` hibaként (súly 10, teljes traceback az üzenetben) jelenik meg, a program nem áll le.

## 4. Az alsó réteg: lexer és objektum-elemző

### 4.1 Token-olvasó: `parse_pdf_param(d, p, pend, err)` (374. sor)

Egy tokent olvas a `p` pozíciótól; visszaadja az új pozíciót és a tokent. A token típusa a Python-típusából derül ki:

| Visszaadott érték | PDF-elem |
|---|---|
| `int` | szám (max. 20 jegy; a hosszabb "számokat" nem alakítja át, `bytes` marad) |
| `bytes` `/`-jellel kezdve | név (`/Type`); a `#xx` hex-escape feloldva (`/J#53` → `/JS`) |
| `bytes` egyébként | kulcsszó: `obj`, `endobj`, `stream`, `endstream`, `R`, `null`, `true`, `false`, `xref`, `trailer`, `startxref`, `n`, `f`, illetve `%%EOF` |
| `PDFString` | literál `(...)` vagy hex `<...>` string, feloldott escape-ekkel |
| `'<'` / `'>'` (str) | `<<` és `>>` (a `>` egyedül is `'>'`) |
| `'['` `']'` `'{'` `'}'` `')'` (str) | határolók |
| `None` | vége az adatnak |

Jellemző engedékenységek: `endobj` után nem kell whitespace; a lezáratlan `(` string a sor végén záródik (Oracle PDF driver hibája); a `\ddd` oktális escape 1–3 jegyű; a `\r` és `\r\n` a stringen belül `\n`-re alakul; a hex-stringben a whitespace a két nibble között is megengedett, a páratlan utolsó nibble 0-val egészül ki; a kommentek eldobódnak, kivéve a `%%EOF`-ot, ami tokenként visszajön.

### 4.2 Objektum-olvasó: `parse_pdf_obj(d, p, pend, stop, err)` (595. sor)

Tokeneket gyűjt egy listába (`objs`), amíg a `stop` (tipikusan `endobj`), `%%EOF` vagy a `startxref <szám>` páros el nem jön. Visszatérés: `(pozíció, objs, stream)`.

Fontos részletek:

- **Újraszinkronizálás**: ha a token-listán belül újabb `N G obj` fejléc jön (az előző obj-ból hiányzik az `endobj`), az `N` előtt megáll, és onnan folytatható a következő olvasás.
- **Hosszú `null`/`true`/`false` sorozatok** (pl. tagged PDF `/ParentTree`, százmillió byte is lehet) átugrása byte-összehasonlítással (`NULLCHUNKS`) és regex-szel, mert ezekben szintaktikai hiba nem lehet és elemzéshez nem kellenek.
- **Stream-adat kijelölése** a `stream` kulcsszó után:
  1. a `/Length` közvetlen értéke; ha `L` byte után (legfeljebb 8 whitespace-t átugorva) ott az `endstream`, a hossz jó, és a stream adataiban előforduló `endstream` szöveg sem zavar;
  2. különben az első `endstream` szövegig tart a stream (`d.find`), és a `/Length` eltérését hibaként jelzi (`STREAM: invalid length ...`); 1–3 byte rövidebb `/Length` (a záró sorvége) nem hiba;
  3. hivatkozott `/Length` (`12 0 R`) esetén a hossz ismeretlennek számít (negatív `stream_len`), mindig az `endstream` keresése dönt;
  4. ha nincs `endstream`, de a `/Length` a fájlba fér, azt használja (hiba); egyébként hiba, és a stream-adatot tokenként olvassa tovább.
  
  A `PDFStream` objektumba a nyers adat, a `/Filter` lista, a `/DecodeParms` és a pozíciók (`pos`, `endpos`, `declared`) kerülnek; ez utóbbiak a sorvége-sérülés felismeréséhez kellenek.

### 4.3 Segédfüggvények a token-listán

- `objs_value(objs, name)` (518): az első `name` kulcs utáni érték listaként (tömb esetén annak elemei). Nem ismeri a beágyazás mélységét: az első előfordulást veszi.
- `objs_dict(objs, i)` (537): az `objs[i]`-nél kezdődő `<< >>` **legfelső szintjének** kulcs–érték párjai. Beágyazott dict/tömb értéke `None`, a hivatkozás `('R', oid)`.
- `top_dict(objs)` (727): az obj törzsének dict-je, ha az `N G obj <<` alakú.
- `objs_decodeparms(objs)` (567): a `/DecodeParms` (vagy `/DP`) dict; tömb esetén a predictort tartalmazót, különben az elsőt.

## 5. Stream-dekóderek

A `PDFStream.decode(predictor=False)` (332. sor) a `/Filter` lista sorrendjében alkalmazza a szűrőket. Nem dob kivételt: az **első** hiba a `stream.error`-ba, a megjegyzések a `stream.note`-ba kerülnek, és a részlegesen dekódolt adatot adja vissza. A képformátumoknál (`/DCTDecode`, `/JPXDecode`, `/JBIG2Decode`, `/CCITTFaxDecode`) megáll, a `/Crypt`-et átugorja, ismeretlen szűrőt kiír. Az inline-image rövidítéseket (`/Fl`, `/AHx`, `/A85`, `/LZW`, `/RL`) is elfogadja.

| Szűrő | Függvény | Viselkedés |
|---|---|---|
| `/FlateDecode` | `inflate` (226) | Lásd alább. |
| `/LZWDecode` | `LZWDecode` (38) | pypdf-ből átvett, hiba esetén a részeredményt adja vissza (`error`), az EOD utáni maradékot megjegyzi (`note`). `/EarlyChange` alapértelmezett (1) viselkedéssel. |
| `/ASCII85Decode` | `ASCII85Decode` (154) | Megengedő: 32 bites túlcsordulás levágva, `y` (btoa) elfogadva, érvénytelen karakterek átugorva; mindez megjegyzés, nem hiba. |
| `/ASCIIHexDecode` | `ASCIIHexDecode` (125) | Érvénytelen karakter és hiányzó `>` hiba, de dekódol tovább. |
| `/RunLengthDecode` | `RunLengthDecode` (186) | Egyszerű, hibát nem jelez. |
| predictor | `unpredict` (267) | PNG (10–15) és TIFF (2, csak 8 bit) predictor visszaalakítása. Csak `predictor=True` esetén fut: xref stream, objstm, csatolmány. |

### 5.1 A Flate-kibontás logikája (`inflate`)

A cél: úgy viselkedni, mint a PDF-olvasók, de a **valódi adatsérülést** hibának jelezni.

1. Üres (csak whitespace) stream → üres adat, nem hiba.
2. Normál zlib-kibontás 4 KB-os darabokban (hiba esetén a hibáig kibontott rész megmarad). Ha végigért, kész.
3. Ha nem: nyers deflate (32 K ablak) a zlib-fejléc nélkül, és az Adler-32 ellenőrzőösszeg **kézi** ellenőrzése:
   - hiányzó/csonka checksum → megjegyzés (valid fájlokban is előfordul, az olvasók nem ellenőrzik);
   - checksum-eltérés → **hiba** (adatsérülés gyanúja, a deflate-nek nincs más ellenőrzése);
   - túl kicsi ablakméret a fejlécben (`invalid distance too far back`), de 32 K-val jó → megjegyzés;
   - zlib-fejléc nélküli nyers deflate → megjegyzés;
   - `00 00 FF FF` a végén (sync flush, nem lezárt folyam) → megjegyzés.
4. Ha semmi nem jön össze: menti, ami menthető (256 byte-os darabolással), és hibát ad (`ZLIB: <zlib hiba> (decoded N bytes)` vagy `ZLIB: truncated stream?`).

## 6. Az xref kezelése

### 6.1 Szekciók (`parse_xref`, 1017)

A `startxref` offsetjétől indulva egy `todo` listán követi a `/Prev` és `/XRefStm` láncot (a már látott offseteket kihagyja, így a hurkok nem végtelenek). Két formátum:

- **ASCII tábla** (`parse_xref_table`, 1076): `xref` + alszekció-fejlécek + `offset gen n|f` sorok + `trailer <<...>>`. Az `n` bejegyzések `xref[oid] = (offset+base, gen)`; a `setdefault` miatt a legfrissebb szekció győz. A `0 offsetű n` bejegyzések külön halmazba (`xref_zero`) kerülnek. Javítja azt a gyakori hibát, amikor az első szabad bejegyzés 1-es sorszámmal indul 0 helyett.
- **Xref stream** (`parse_xref_stream`, 1102): `/W [a b c]`, `/Index`, `/Size` alapján olvassa a bináris sorokat. 1-es típus → `xref`, 2-es típus → `xref_stm[oid] = (objstm oid, index)`, 1-es típus 0 offsettel → `xref_zero`. A dekódolási hiba itt súlyos (10).

A trailerből a `/Encrypt` objektum számát is kiolvassa (`self.encrypt`); titkosított fájlnál a streamek dekódolását és az objstm-ek bontását kihagyja, a csatolmányt nyersen adja ki.

Ha a `startxref` rossz helyre mutat, a fájl végéről visszafelé keresi az utolsó `xref` szót (két helyen: `parse()` 893. sor, `parse_xref` 1050. sor).

### 6.2 Ellenőrzés (`verify_xref`, 1138 és `report_badxref`, 1160)

Minden bejegyzésre megnézi, hogy az offseten (max. 4 whitespace-t megengedve) tényleg `N G obj` áll-e a **megegyező** obj-számmal és generációval. A rossz bejegyzések a `badxref` halmazba kerülnek, és **összesítve** kapnak hibát, jelleg szerint: minden bejegyzés egy offsetre mutat (hamis tábla), más objektumokra mutat (elszámozott tábla), az objektum elé mutat (max. K byte-tal), egyéb.

`check_xref_zero` (1299): a 0 offsetű `n` bejegyzés nem hiba, ha az obj nem is létezik (macOS Quartz így jelöli a használaton kívüli számokat), de hiba, ha az obj létezik a fájlban.

`verify_xref_stm` (1189): a 2-es típusú bejegyzések objstm-jei léteznek-e, és tényleg benne van-e az obj.

### 6.3 Bejárás: `walk_xref` vs. `scan_objs`

- `walk_xref` (935): csak validate módban, ha az xref legfeljebb felerészben rossz és a fájl nem csonka. A rossz offsetű obj-eket regex-szel megkeresi a fájlban (az utolsó előfordulást). Az `endobj` nélküli obj-eket egy összesített hibával jelzi.
- `scan_objs` (955): lineáris végigolvasás a fejléctől. Az első obj előtti szemetet átugorja (első 1 KB-on belül), az obj-ok közti szemétnél az `N G obj`-ra szinkronizál (hiba). A `%%EOF` utáni régi PDF-maradékban (`leftover`) a hibákat nem számolja (`quiet`), de a tartalmat kinyeri.

`process_obj` (988) tölti a `dom[oid] = (kezdet, vég)` térképet (a későbbi előfordulás felülírja, mint az incremental update-nél), majd `parse_stream` és `analyze_obj`.

## 7. Tartalom-elemzés

### 7.1 `parse_stream` (1199)

Minden streamet kibont (a tömörítési hibák miatt), kivéve a titkosított fájlokat és az xref streameket (azokat a `parse_xref` már ellenőrizte). A dekódolási hiba `STREAM: decoding error in obj #N` hiba, a megjegyzés csak kiírás. `/EmbeddedFile` → `content`; `/ObjStm` → `parse_objstm`. Előtte `check_stream_length` a sorvége-statisztikához.

### 7.2 Objektum-streamek (`parse_objstm`, 1406)

A `/First` byte-os fejlécből (`oid offset` párok) kiolvassa a belső obj-eket, és mindegyiket `analyze_obj`-nak adja (a következő obj offsetje a vége). Hibás fejléc esetén az egészet egyben elemzi. A belső obj-ek a `strobjs` és `fsobjs` térképekbe kerülhetnek, a `dom`-ba nem.

### 7.3 `analyze_obj` (1440)

Egy obj token-listáján:

- **string-obj**: ha az obj törzse egy string, `strobjs[oid]` (a `/JS 12 0 R` és Launch feloldáshoz); `http`/`script` tartalmút kiír;
- **`/URI`**: kiírja (string) vagy megjegyzi a hivatkozott obj-t;
- **Filespec** (`/Type /Filespec` vagy `/UF`): a neveit `fsobjs`-be teszi, és beállítja a **következő** csatolmány nevét (`streamname`), illetve visszamenőleg átnevezi az utolsó `pdfstream.dat` tartalmat;
- **`/JS`**: minden előfordulás; string → azonnal `pdfstream.js`, hivatkozás → `jsrefs`, a végén `resolve_js` oldja fel (string-obj vagy stream);
- **`/S /Launch`**: az egész action dict-et bejárja, a `/F`, `/Win`, `/Unix`, `/Mac`, `/P`, `/D`, `/O` értékeket összefűzi; a hivatkozott `/F`-et a végén `resolve_launch` oldja fel (Filespec nevei, string-obj, vagy az obj összes stringje);
- **oldalak**: `/Type /Page` számlálás, `/Type /Pages /Count` maximuma.

A HTML-t (`add_html`, 786) egészében és a `<script>` blokkjait külön JS-ként is kiadja.

## 8. Sérülés-felismerő heurisztikák

Ezek egy-egy jól ismert, egyetlen okra visszavezethető sérülésmintát ismernek fel, és a sok kis hiba helyett **egy** magyarázó, 10-es súlyú hibát adnak a lista elejére.

| Üzenet | Függvény | Mit ismer fel |
|---|---|---|
| `TRUNCATED: ...` | `check_truncated` (1371) | Félbeszakadt letöltés/másolás: nullákkal kitöltött vége, vagy PDF-szerkezet az utolsó `%%EOF` után (pl. linearizált fájlból csak az első oldal szekciója teljes). Ha viszont az EOF előtti dokumentum teljes (jó xref) és az EOF utáni maradék nem obj-határon kezdődik, az régi/másik fájl maradéka: `JUNK ... leftover` (súly 1). A `startxref N`-nel végződő, csak `%%EOF`-hiányos fájl nem csonka. |
| `TRANSFER: LF -> CRLF / CRLF -> LF ...` | `check_transfer` (1329), `check_xref_shift`, `check_stream_length`, `find_last_xref` | Szöveges módú átvitel (base64 nélküli e-mail, ASCII FTP): a streamek hossza pont a bennük levő sorvégek számával tér el a `/Length`-től, a `startxref` és az xref-bejegyzések monoton növekvő mértékben csúsznak. Több egybehangzó jel kell, az ellentmondó jelek elnyomják. |
| `GAP: N bytes missing/inserted inside the stream at offset ...` | `check_gap` (1226) | Minden rossz xref-bejegyzés **ugyanannyival** csúszik, és egy korábbi stream hossza pont ennyivel tér el: sérült másolás egy stream belsejében. |
| `JUNK: N bytes before the %PDF header (...)` | `junk_kind` (708) | UTF-8 BOM, RTFD, ZIP, OLE2, RTF, MacBinary, HTML, MIME-fejlécek, szöveg vagy bináris a fejléc előtt. Ha az offsetek a fejléchez relatívak, `base` beállítása. |
| `JUNK: N bytes after %%EOF (HTML)` | `parse` | HTML a PDF után (poliglott fájl); tartalomként is kiadja. |

## 9. Ismert tervezési korlátok

- A hivatkozott `/Length` (`12 0 R`) értékét **nem oldja fel**, ilyenkor az első `endstream` szöveg dönt (lásd 10.3).
- A csatolmány és a Filespec **sorrend alapján** párosul, nem a `/EF << /F 12 0 R >>` hivatkozás alapján (lásd 10.4).
- Titkosított PDF-nél nincs dekódolás; a `/Encrypt` csak a trailerből derül ki, a `scan_objs` közben talált `trailer`-ből nem.
- Az LZW `/EarlyChange 0` paramétert nem kezeli (a 114. sor a pypdf alapértelmezett, EarlyChange=1 viselkedése).
- A TIFF-predictor csak 8 bit/komponensre működik; a `/DecodeParms` hivatkozott (`R`) értékei `TypeError`-t, azaz hamis `Predictor:` hibát adnak.
- Inline image (`BI ... ID ... EI`) csak tartalom-streamen belül létezik, amit a program nem elemez; a 633. sor ága gyakorlatilag csak szemétnél fut, és a teljes `objs` listát kiírja.
- Validate módban (`walk_xref`) a rejtett, xref-ben nem szereplő obj-ok nem kerülnek elő (szándékos: a szerkesztett PDF-ben ugyanaz az obj többször is szerepel, csak az utolsó érvényes).
- Minden streamet kibont (képeket is), ami nagy fájloknál lassú, de ez a tömörítési hibák felderítéséhez kell.

## 10. Talált hibák és javítási javaslatok

Prioritás szerint. Minden pont az adott bemenettel **reprodukálva** lett (a tesztek a projekt könyvtárán kívül futottak, a forrás nem változott).

### 10.1 A buffer legvégén álló szám/kulcsszó utolsó karaktere elveszik – `parse_pdf_param`, 501–510. sor

A "read BODY" ciklus a következő karaktert **előreolvassa**, mielőtt az aktuálist hozzáfűzné; ha a token pontosan a `pend`-nél ér véget, a ciklus a hozzáfűzés előtt kilép. Egykarakteres token esetén üres `b''` jön vissza.

Reprodukció:

```
parse_pdf_obj(b'123',0,3)     -> [12]
parse_pdf_obj(b'true',0,4)    -> [b'tru']
parse_pdf_obj(b'12 0 R',0,6)  -> [12, 0, b'']
objstm: '1 0 2 8 <</A 1>>612'  -> #2 = 61   (612 helyett)
```

Hatás: az objektum-streamek **utolsó** obj-ának utolsó tokenje (ha a stream nem whitespace-szel végződik), valamint az `objstm` régió-határon záródó tokenek. A fő fájlnál ritkán érint (a `%%EOF` és az `endobj` külön kezelt), de pl. egy `/Count 5`-tel záródó objstm-obj oldalszáma elveszik. Javítás: a ciklus szerkezetének megfordítása (először hozzáfűzés, majd a következő karakter vizsgálata `p<pend` feltétellel), vagy a ciklus után a hiányzó utolsó karakter hozzáfűzése.

### 10.2 Nem numerikus `startxref` érték → 10-es súlyú "exception" hiba tracebackkel – `parse`, 851. sor

Az `int(d[o:q])` a külső `try`-ba esik, így pl. egy `startxref\n%%EOF` (hiányzó offset) fájl `XREF: exception!!! Traceback ...` hibát kap (súly 10), és az xref-keresési tartalék (`rfind(b'xref')`) sem fut le. Ezen felül a `parse_pdf_obj` is ad egy `Xref: INVALID offset format` hibát ugyanerre.

Reprodukció: minimális fájl `startxref\n%%EOF` végződéssel → `errcnt=11`, az első hiba egy traceback.

Javítás: az `int()` külön `try`-ba, célzott üzenettel (`XREF: invalid startxref value`), és utána a meglévő `rfind`-es tartalék futtatása.

### 10.3 Hivatkozott `/Length` + tömörítetlen csatolmány, amelyben `endstream` szerepel → a csatolmány csonkul – `parse_pdf_obj`, 642. és 667. sor

Indirekt `/Length` esetén mindig az első `endstream` szövegig tart a stream. Egy tömörítetlen `/EmbeddedFile`, amely maga is PDF (vagy bármi, amiben `endstream` van), az első belső `endstream`-nél elvágódik; a maradékot a lexer tokenként olvassa, és hamis `INVALID object type: b'endstream'` hibák keletkeznek.

Reprodukció: beágyazott 60 byte-os mini-PDF, `/Length 5 0 R` → 38 byte tartalom, plusz a hamis hiba.

Javítás: a hivatkozott `/Length` feloldása (a `/Length N 0 R` obj tipikusan közvetlenül a stream után áll: `N 0 obj <szám> endobj`; egy egyszerű regex-keresés `rb'N 0 obj\s*(\d+)\s*endobj'` a stream utáni néhány száz byte-on, vagy az xref/`dom` alapján), és ha a kapott hossz után ott az `endstream`, azt használni, ahogy a közvetlen `/Length`-nél már történik.

### 10.4 Csatolmány-nevek sorrend alapján párosulnak – `analyze_obj`, 1470–1477. sor és `parse_stream`, 1216–1218. sor

A Filespec neve a `streamname` változóba kerül, és a **következő** `/EmbeddedFile` kapja meg; utólag csak az utolsó `pdfstream.dat` nevezhető át. Két Filespec, majd két stream sorrendnél az első stream a második nevét kapja, a második névtelen marad. A kód a `/EF << /F 3 0 R >>` hivatkozást (amely egyértelműen összeköti a kettőt) nem használja. A szerző maga is jelzi ("hu de gany").

Reprodukció: Filespec(alpha, EF→3), Filespec(beta, EF→4), stream 3 (`AAAAA`), stream 4 (`BBBBB`) → `[(AAAAA, 'beta.txt'), (BBBBB, 'pdfstream.dat')]`.

Javítás: az `/EF` dict `/F`/`/UF` hivatkozásának kiolvasása (`objs_dict` a beágyazott dict-et `None`-nak adja, ezért a token-listán kell megkeresni), `fsobjs`-hez hasonló `oid → név` térkép, és a `content` bejegyzés a stream **obj-száma** alapján kapja a nevét a feldolgozás végén (a `resolve_js` mintájára). Ez a többszörös csatolmányú (pl. PDF/A-3, ZUGFeRD) fájloknál számít.

### 10.5 `rfind(b'xref')` a korábbi `startxref` kulcsszóban is talál – 893., 1050. és 1270. sor

A `xref` részstringje a `startxref`-nek. Ha a fájl csak xref streameket használ (nincs ASCII `xref`), de van benne korábbi incremental-update `startxref`, a három tartalék-keresés annak belsejébe mutat: a `parse_xref` ezt `xref` táblaként próbálja olvasni (`invalid subsection header` hiba), a `find_last_xref` pedig hamis `startxref`-eltolást számol a `TRANSFER` heurisztikának.

Reprodukció: két szekciós fájl, a második xref stream → `rfind` = a korábbi `startxref`+5.

Javítás: a `re_structure`-ben már használt `(?<![a-z])xref` minta (regex `finditer`, utolsó találat), vagy a találat előtti karakter ellenőrzése.

### 10.6 `self.startxref` a `base` korrekció előtti értéket tárolja – 852. sor

Ha a fejléc előtt szemét van és az offsetek a fejléchez relatívak (`base=hdr`), a `self.startxref[0]` a korrigálatlan `o`. A `check_transfer` ezt hasonlítja a `find_last_xref` abszolút pozíciójához, így `xdelta = hdr` hamis eltolás jön ki, ami befolyásolhatja a `TRANSFER` döntést (`xok`, `xsign`) és az üzenet `startxref off by N` részét.

Reprodukció: 8 byte szemét + helyes fájl → `base=8`, `startxref=(40, ...)`, `find_last_xref=48`.

Javítás: a `self.startxref` beállítása a `o+=hdr` után (vagy `o+self.base` használata a `check_transfer`-ben).

### 10.7 Négyzetes idejű részek nagy, sérült fájlokon

- `walk_xref`, 941. sor: minden rossz xref-bejegyzésre külön teljes-fájl regex keresés (`badxref × fájlméret`). A már meglévő, gyorsítótárazott `obj_starts()` térkép ugyanezt adja egy menetben.
- `check_xref_shift`, 1325–1326. sor: minden sorra `d.count(b'\r\n', 0, P)` a fájl elejétől (`badxref × fájlméret`). A sorok pozíció szerint rendezettek, így a számlálás inkrementálisan, egy menetben elvégezhető.

Egy 50 MB-os, több ezer rossz bejegyzésű fájlnál ez percekig tarthat. Nem helyességi hiba, de a "megengedő, mindent feldolgoz" célnak ellentmond.

### 10.8 Kisebb észrevételek

- **`deflate_complete`** (263) és az **`import base64`** (23) nem használt.
- **`parse_objstm`**, 1431. sor: az obj végét a fejléc **következő** párjának offsetje adja, azaz feltételezi, hogy a párok offset szerint növekvők. A specifikáció ezt nem írja elő; nem rendezett fejlécnél hamis `invalid offset` hibák jönnek. Javítás: a párok rendezése offset szerint a határok kiszámításához.
- **Fejléc az első 1024 byte-on túl** (`deep_header`): a fejléc-sor és a bináris komment átugrása a 816. és 832. sor abszolút `p<1024` korlátja miatt nem fut, így `bad pdf header!` íródik ki és a `binheader` hamis lesz; a `scan_objs` 959. sorának kezdő szemét-átugrása is üres tartományt kap. Javítás: a korlát a `hdr+1024` legyen.
- **`os.listdir`** (1598): nem létező útvonalnál a program a feldolgozás előtt kivétellel leáll; a könyvtár alkönyvtárait is fájlként próbálja megnyitni (kezelt kivétel, de zajos).
- **`analyze_obj` /Launch**: `objs[i-2]` `i=1`-nél `objs[-1]`-re hivatkozik (ártalmatlan, de véletlen egyezést adhat).
- **Kiírások mérete**: a `JSCR:` és az `embedded image` sorok a teljes adatot kiírják (több MB-os JS-nél zajos); a többi helyen már van `[:256]` levágás.
