# pdfparse3.py – működési dokumentáció

*Készült: 2026-09-24, a `pdfparse3.py` eredeti (1610 soros, `d993039`) változatának átnézése alapján; ugyanazon a napon frissítve a 10.1–10.7 hibák javítása után (1679 sor). A sorszámok a javított változatra vonatkoznak, kivéve ahol jelezve van.*

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

## 3. A feldolgozás menete (`PDFParser.parse`, 813. sor)

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
name_files        – a csatolmányok neve a Filespec /EF hivatkozásai szerint
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

### 4.2 Objektum-olvasó: `parse_pdf_obj(d, p, pend, stop, err, lenref)` (599. sor)

Tokeneket gyűjt egy listába (`objs`), amíg a `stop` (tipikusan `endobj`), `%%EOF` vagy a `startxref <szám>` páros el nem jön. Visszatérés: `(pozíció, objs, stream)`.

Fontos részletek:

- **Újraszinkronizálás**: ha a token-listán belül újabb `N G obj` fejléc jön (az előző obj-ból hiányzik az `endobj`), az `N` előtt megáll, és onnan folytatható a következő olvasás.
- **Hosszú `null`/`true`/`false` sorozatok** (pl. tagged PDF `/ParentTree`, százmillió byte is lehet) átugrása byte-összehasonlítással (`NULLCHUNKS`) és regex-szel, mert ezekben szintaktikai hiba nem lehet és elemzéshez nem kellenek.
- **Stream-adat kijelölése** a `stream` kulcsszó után:
  1. a `/Length` közvetlen értéke; ha `L` byte után (legfeljebb 8 whitespace-t átugorva) ott az `endstream`, a hossz jó, és a stream adataiban előforduló `endstream` szöveg sem zavar;
  2. különben az első `endstream` szövegig tart a stream (`d.find`), és a `/Length` eltérését hibaként jelzi (`STREAM: invalid length ...`); 1–3 byte rövidebb `/Length` (a záró sorvége) nem hiba;
  3. hivatkozott `/Length` (`12 0 R`) esetén a `lenref` visszahívás (`PDFParser.resolve_length`, 1324. sor) az `obj_starts()` gyorsítótárból megkeresi a `N G obj <szám> endobj` objektumot (az utolsó előfordulást), és a kapott hosszat az 1. pont szerint, csak `endstream`-mel megerősítve használja; ha nincs ilyen obj vagy a hossz nem stimmel, az `endstream` keresése dönt;
  4. ha nincs `endstream`, de a `/Length` a fájlba fér, azt használja (hiba); egyébként hiba, és a stream-adatot tokenként olvassa tovább.
  
  A `PDFStream` objektumba a nyers adat, a `/Filter` lista, a `/DecodeParms` és a pozíciók (`pos`, `endpos`, `declared`) kerülnek; ez utóbbiak a sorvége-sérülés felismeréséhez kellenek.

### 4.3 Segédfüggvények a token-listán

- `objs_value(objs, name)` (521): az első `name` kulcs utáni érték listaként (tömb esetén annak elemei). Nem ismeri a beágyazás mélységét: az első előfordulást veszi.
- `objs_dict(objs, i)` (540): az `objs[i]`-nél kezdődő `<< >>` **legfelső szintjének** kulcs–érték párjai. Beágyazott dict/tömb értéke `None`, a hivatkozás `('R', oid)`.
- `top_dict(objs)` (743): az obj törzsének dict-je, ha az `N G obj <<` alakú.
- `objs_decodeparms(objs)` (570): a `/DecodeParms` (vagy `/DP`) dict; tömb esetén a predictort tartalmazót, különben az elsőt.

## 5. Stream-dekóderek

A `PDFStream.decode(predictor=False)` (332. sor) a `/Filter` lista sorrendjében alkalmazza a szűrőket. Nem dob kivételt: az **első** hiba a `stream.error`-ba, a megjegyzések a `stream.note`-ba kerülnek, és a részlegesen dekódolt adatot adja vissza. A képformátumoknál (`/DCTDecode`, `/JPXDecode`, `/JBIG2Decode`, `/CCITTFaxDecode`) megáll, a `/Crypt`-et átugorja, ismeretlen szűrőt kiír. Az inline-image rövidítéseket (`/Fl`, `/AHx`, `/A85`, `/LZW`, `/RL`) is elfogadja.

| Szűrő | Függvény | Viselkedés |
|---|---|---|
| `/FlateDecode` | `inflate` (226) | Lásd alább. |
| `/LZWDecode` | `LZWDecode` (40) | pypdf-ből átvett; a `/DecodeParms /EarlyChange` (0/1) paramétert kezeli. Érvénytelen kód hiba (a részeredményt adja vissza). A hiányzó EOD (záró) kód **megjegyzés**, ha a stream vége a `/Length` alapján biztos (`PDFStream.exact`: a `/Length` után ott az `endstream`), mert akkor az író pont ennyit írt ki (mint a Flate-nél a hiányzó adler32); ha az `endstream` keresése döntött, **hiba**, mert csonkolásra is utalhat. Az EOD utáni maradékot is megjegyzi. |
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

### 6.1 Szekciók (`parse_xref`, 1051)

A `startxref` offsetjétől indulva egy `todo` listán követi a `/Prev` és `/XRefStm` láncot (a már látott offseteket kihagyja, így a hurkok nem végtelenek). Két formátum:

- **ASCII tábla** (`parse_xref_table`, 1110): `xref` + alszekció-fejlécek + `offset gen n|f` sorok + `trailer <<...>>`. Az `n` bejegyzések `xref[oid] = (offset+base, gen)`; a `setdefault` miatt a legfrissebb szekció győz. A `0 offsetű n` bejegyzések külön halmazba (`xref_zero`) kerülnek. Javítja azt a gyakori hibát, amikor az első szabad bejegyzés 1-es sorszámmal indul 0 helyett.
- **Xref stream** (`parse_xref_stream`, 1136): `/W [a b c]`, `/Index`, `/Size` alapján olvassa a bináris sorokat. 1-es típus → `xref`, 2-es típus → `xref_stm[oid] = (objstm oid, index)`, 1-es típus 0 offsettel → `xref_zero`. A dekódolási hiba itt súlyos (10).

A trailerből a `/Encrypt` objektum számát is kiolvassa (`self.encrypt`); titkosított fájlnál a streamek dekódolását és az objstm-ek bontását kihagyja, a csatolmányt nyersen adja ki.

Ha a `startxref` rossz helyre mutat, visszafelé keresi az utolsó `xref` kulcsszót (`rfind_xref`, 1038. sor; a `startxref` belsejében levő találat nem számít), két helyen: `parse()` 918. sor, `parse_xref` 1084. sor.

### 6.2 Ellenőrzés (`verify_xref`, 1172 és `report_badxref`, 1194)

Minden bejegyzésre megnézi, hogy az offseten (max. 4 whitespace-t megengedve) tényleg `N G obj` áll-e a **megegyező** obj-számmal és generációval. A rossz bejegyzések a `badxref` halmazba kerülnek, és **összesítve** kapnak hibát, jelleg szerint: minden bejegyzés egy offsetre mutat (hamis tábla), más objektumokra mutat (elszámozott tábla), az objektum elé mutat (max. K byte-tal), egyéb.

`check_xref_zero` (1341): a 0 offsetű `n` bejegyzés nem hiba, ha az obj nem is létezik (macOS Quartz így jelöli a használaton kívüli számokat), de hiba, ha az obj létezik a fájlban.

`verify_xref_stm` (1223): a 2-es típusú bejegyzések objstm-jei léteznek-e, és tényleg benne van-e az obj.

### 6.3 Bejárás: `walk_xref` vs. `scan_objs`

- `walk_xref` (962): csak validate módban, ha az xref legfeljebb felerészben rossz és a fájl nem csonka. A rossz offsetű obj-eket az `obj_starts()` gyorsítótárból keresi meg (az utolsó előfordulást). Az `endobj` nélküli obj-eket egy összesített hibával jelzi.
- `scan_objs` (982): lineáris végigolvasás a fejléctől. Az első obj előtti szemetet átugorja (első 1 KB-on belül), az obj-ok közti szemétnél az `N G obj`-ra szinkronizál (hiba). A `%%EOF` utáni régi PDF-maradékban (`leftover`) a hibákat nem számolja (`quiet`), de a tartalmat kinyeri.

`process_obj` (1015) tölti a `dom[oid] = (kezdet, vég)` térképet (a későbbi előfordulás felülírja, mint az incremental update-nél), majd `parse_stream` és `analyze_obj`.

## 7. Tartalom-elemzés

### 7.1 `parse_stream` (1233)

Minden streamet kibont (a tömörítési hibák miatt), kivéve a titkosított fájlokat és az xref streameket (azokat a `parse_xref` már ellenőrizte). A dekódolási hiba `STREAM: decoding error in obj #N` hiba, a megjegyzés csak kiírás. `/EmbeddedFile` → `content` (először `pdfstream.dat` néven; a stream obj-száma a `filestreams` térképbe kerül, a végleges nevet a `name_files` adja a feldolgozás végén); `/ObjStm` → `parse_objstm`. Előtte `check_stream_length` a sorvége-statisztikához.

### 7.2 Objektum-streamek (`parse_objstm`, 1465)

A `/First` byte-os fejlécből (`oid offset` párok) kiolvassa a belső obj-eket, és mindegyiket `analyze_obj`-nak adja (a következő obj offsetje a vége). Hibás fejléc esetén az egészet egyben elemzi. A belső obj-ek a `strobjs` és `fsobjs` térképekbe kerülhetnek, a `dom`-ba nem.

### 7.3 `analyze_obj` (1499)

Egy obj token-listáján:

- **string-obj**: ha az obj törzse egy string, `strobjs[oid]` (a `/JS 12 0 R` és Launch feloldáshoz); `http`/`script` tartalmút kiír;
- **`/URI`**: kiírja (string) vagy megjegyzi a hivatkozott obj-t;
- **Filespec** (`/Type /Filespec` vagy `/UF`): a neveit `fsobjs`-be teszi, és az `/EF << /F 3 0 R /UF 3 0 R >>` által hivatkozott stream obj-számaihoz rendeli a fájlnevet (`efnames`; a későbbi Filespec győz). Ha az `/EF` maga is hivatkozás (`/EF 39 0 R`, külön objektumban álló `<< /F 47 0 R >>` dict), a nevet `efrefs`-be teszi, az ilyen, csak `/F` `/UF` `/DOS` `/Mac` `/Unix` kulcsú dict-eket pedig `efdicts`-be. A feldolgozás végén a `name_files` először az `efrefs`→`efdicts` láncot oldja fel, majd ezzel nevezi el a `/EmbeddedFile` tartalmakat; a Filespec nélküli stream `pdfstream.dat` marad;
- **`/JS`**: minden előfordulás; string → azonnal `pdfstream.js`, hivatkozás → `jsrefs`, a végén `resolve_js` oldja fel (string-obj vagy stream);
- **`/S /Launch`**: az egész action dict-et bejárja, a `/F`, `/Win`, `/Unix`, `/Mac`, `/P`, `/D`, `/O` értékeket összefűzi; a hivatkozott `/F`-et a végén `resolve_launch` oldja fel (Filespec nevei, string-obj, vagy az obj összes stringje);
- **oldalak**: `/Type /Page` számlálás, `/Type /Pages /Count` maximuma.

A HTML-t (`add_html`) egészében és a `<script>` blokkjait külön JS-ként is kiadja.

## 8. Sérülés-felismerő heurisztikák

Ezek egy-egy jól ismert, egyetlen okra visszavezethető sérülésmintát ismernek fel, és a sok kis hiba helyett **egy** magyarázó, 10-es súlyú hibát adnak a lista elejére.

| Üzenet | Függvény | Mit ismer fel |
|---|---|---|
| `TRUNCATED: ...` | `check_truncated` (1430) | Félbeszakadt letöltés/másolás: nullákkal kitöltött vége, vagy PDF-szerkezet az utolsó `%%EOF` után (pl. linearizált fájlból csak az első oldal szekciója teljes). Ha viszont az EOF előtti dokumentum teljes (jó xref) és az EOF utáni maradék nem obj-határon kezdődik, az régi/másik fájl maradéka: `JUNK ... leftover` (súly 1). A `startxref N`-nel végződő, csak `%%EOF`-hiányos fájl nem csonka. |
| `TRANSFER: LF -> CRLF / CRLF -> LF ...` | `check_transfer` (1388), `check_xref_shift` (a sorvégeket a `counts_upto` egy menetben számolja), `check_stream_length`, `find_last_xref` | Szöveges módú átvitel (base64 nélküli e-mail, ASCII FTP): a streamek hossza pont a bennük levő sorvégek számával tér el a `/Length`-től, a `startxref` és az xref-bejegyzések monoton növekvő mértékben csúsznak. Több egybehangzó jel kell, az ellentmondó jelek elnyomják. |
| `GAP: N bytes missing/inserted inside the stream at offset ...` | `check_gap` (1260) | Minden rossz xref-bejegyzés **ugyanannyival** csúszik, és egy korábbi stream hossza pont ennyivel tér el: sérült másolás egy stream belsejében. |
| `JUNK: N bytes before the %PDF header (...)` | `junk_kind` (724) | UTF-8 BOM, RTFD, ZIP, OLE2, RTF, MacBinary, HTML, MIME-fejlécek, szöveg vagy bináris a fejléc előtt. Ha az offsetek a fejléchez relatívak, `base` beállítása. |
| `JUNK: N bytes after %%EOF (HTML)` | `parse` | HTML a PDF után (poliglott fájl); tartalomként is kiadja. |

## 9. Ismert tervezési korlátok

- A hivatkozott `/Length` feloldása a fájlban levő `N G obj <szám> endobj` alakú objektumra épül; ha a hossz-objektum egy object streamben van, nem találja meg, és az `endstream` keresése dönt (mint régen).
- Titkosított PDF-nél nincs dekódolás; a `/Encrypt` csak a trailerből derül ki, a `scan_objs` közben talált `trailer`-ből nem.
- A csonkolt LZW stream csak a hosszából ismerhető fel: az LZW-ben nincs záró blokk vagy ellenőrzőösszeg, ezért a hiányzó EOD kód megjegyzés, ha a `/Length` biztosan megadja a stream végét, és hiba, ha nem (10.9). Egy `/Length`-hez igazított, de csonka LZW adat (pl. `samples/lzw_trunc.pdf`) így nem hiba. A Flate-nél a hiányzó záró blokk mindig hiba.
- A TIFF-predictor csak 8 bit/komponensre működik; a `/DecodeParms` hivatkozott (`R`) értékei `TypeError`-t, azaz hamis `Predictor:` hibát adnak.
- Inline image (`BI ... ID ... EI`) csak tartalom-streamen belül létezik, amit a program nem elemez; a 637. sor ága gyakorlatilag csak szemétnél fut, és a teljes `objs` listát kiírja.
- Validate módban (`walk_xref`) a rejtett, xref-ben nem szereplő obj-ok nem kerülnek elő (szándékos: a szerkesztett PDF-ben ugyanaz az obj többször is szerepel, csak az utolsó érvényes).
- Minden streamet kibont (képeket is), ami nagy fájloknál lassú, de ez a tömörítési hibák felderítéséhez kell.

## 10. Talált hibák és állapotuk

Prioritás szerint. Minden pont az adott bemenettel **reprodukálva** lett. A **10.1–10.7 és 10.9–10.11 javítva** (2026-09-24, egy-egy külön commit), regressziós tesztjeik a `samples_new/` könyvtárban vannak (11. fejezet). A 10.8 megmaradt apróságai még nyitottak. A 10.1–10.7 pontokban a sorszámok az **eredeti** (`d993039`) változatra vonatkoznak.

### 10.1 A buffer legvégén álló szám/kulcsszó utolsó karaktere elveszik – `parse_pdf_param`, 501–510. sor

> **Javítva**: commit `22a058a`. Teszt: `samples_new/01_objstm_last_token.pdf`.

A "read BODY" ciklus a következő karaktert **előreolvassa**, mielőtt az aktuálist hozzáfűzné; ha a token pontosan a `pend`-nél ér véget, a ciklus a hozzáfűzés előtt kilép. Egykarakteres token esetén üres `b''` jön vissza.

Reprodukció:

```
parse_pdf_obj(b'123',0,3)     -> [12]
parse_pdf_obj(b'true',0,4)    -> [b'tru']
parse_pdf_obj(b'12 0 R',0,6)  -> [12, 0, b'']
objstm: '1 0 2 8 <</A 1>>612'  -> #2 = 61   (612 helyett)
```

Hatás: az objektum-streamek **utolsó** obj-ának utolsó tokenje (ha a stream nem whitespace-szel végződik), valamint az `objstm` régió-határon záródó tokenek. A fő fájlnál ritkán érint (a `%%EOF` és az `endobj` külön kezelt), de pl. egy `/Count 5`-tel záródó objstm-obj oldalszáma elveszik. A javítás: a ciklus szerkezetének megfordítása (először hozzáfűzés, majd a következő karakter vizsgálata `p<pend` feltétellel).

### 10.2 Nem numerikus `startxref` érték → 10-es súlyú "exception" hiba tracebackkel – `parse`, 851. sor

> **Javítva**: commit `3250f1b`. Teszt: `samples_new/02_startxref_no_value.pdf`.

Az `int(d[o:q])` a külső `try`-ba esik, így pl. egy `startxref\n%%EOF` (hiányzó offset) fájl `XREF: exception!!! Traceback ...` hibát kap (súly 10), és az xref-keresési tartalék (`rfind(b'xref')`) sem fut le. Ezen felül a `parse_pdf_obj` is ad egy `Xref: INVALID offset format` hibát ugyanerre.

Reprodukció: minimális fájl `startxref\n%%EOF` végződéssel → `errcnt=11`, az első hiba egy traceback.

A javítás: az `int()` külön `try`-ba, célzott üzenettel (`XREF: invalid startxref value`), és utána a meglévő `rfind`-es tartalék futtatása.

### 10.3 Hivatkozott `/Length` + tömörítetlen csatolmány, amelyben `endstream` szerepel → a csatolmány csonkul – `parse_pdf_obj`, 642. és 667. sor

> **Javítva**: commit `06c024d` (`resolve_length`, `lenref`). Teszt: `samples_new/03_indirect_length_embedded_pdf.pdf`.

Indirekt `/Length` esetén mindig az első `endstream` szövegig tart a stream. Egy tömörítetlen `/EmbeddedFile`, amely maga is PDF (vagy bármi, amiben `endstream` van), az első belső `endstream`-nél elvágódik; a maradékot a lexer tokenként olvassa, és hamis `INVALID object type: b'endstream'` hibák keletkeznek.

Reprodukció: beágyazott 60 byte-os mini-PDF, `/Length 5 0 R` → 38 byte tartalom, plusz a hamis hiba.

A javítás: a `PDFParser.resolve_length` az `obj_starts()` gyorsítótárból megkeresi a `N G obj <szám> endobj` objektumot (az utolsó előfordulást), a `parse_pdf_obj` ezt a `lenref` visszahíváson kapja, és a hosszat csak akkor használja, ha utána tényleg `endstream` áll, ahogy a közvetlen `/Length`-nél. A rossz feloldás így nem árt: marad az `endstream` keresése.

### 10.4 Csatolmány-nevek sorrend alapján párosulnak – `analyze_obj`, 1470–1477. sor és `parse_stream`, 1216–1218. sor

> **Javítva**: commit `51c9808` (`efnames`, `filestreams`, `name_files`; a `streamname` megszűnt), kiegészítve a hivatkozott `/EF 39 0 R` alakkal (`efrefs`, `efdicts`), amelyet a privát minták összehasonlító futása hozott elő. Teszt: `samples_new/04a_...`, `04b_...`, `04c_...`.

A Filespec neve a `streamname` változóba kerül, és a **következő** `/EmbeddedFile` kapja meg; utólag csak az utolsó `pdfstream.dat` nevezhető át. Két Filespec, majd két stream sorrendnél az első stream a második nevét kapja, a második névtelen marad. A kód a `/EF << /F 3 0 R >>` hivatkozást (amely egyértelműen összeköti a kettőt) nem használja. A szerző maga is jelzi ("hu de gany").

Reprodukció: Filespec(alpha, EF→3), Filespec(beta, EF→4), stream 3 (`AAAAA`), stream 4 (`BBBBB`) → `[(AAAAA, 'beta.txt'), (BBBBB, 'pdfstream.dat')]`.

A javítás: az `analyze_obj` az `/EF` dict `/F`/`/UF` hivatkozásait `efnames[stream oid] = név` térképbe teszi, a `parse_stream` a csatolmány `content`-indexét obj-szám szerint jegyzi (`filestreams`), és a feldolgozás végén a `name_files` párosítja őket. A `streamname` mechanizmus megszűnt. Ez a többszörös csatolmányú (pl. PDF/A-3, ZUGFeRD) fájloknál számít.

### 10.5 `rfind(b'xref')` a korábbi `startxref` kulcsszóban is talál – 893., 1050. és 1270. sor

> **Javítva**: commit `837eeba` (`rfind_xref`, `re_xrefkw`). Teszt: `samples_new/05_bad_startxref_xrefstream_prev_section.pdf`.

A `xref` részstringje a `startxref`-nek. Ha a fájl csak xref streameket használ (nincs ASCII `xref`), de van benne korábbi incremental-update `startxref`, a három tartalék-keresés annak belsejébe mutat: a `parse_xref` ezt `xref` táblaként próbálja olvasni (`invalid subsection header` hiba), a `find_last_xref` pedig hamis `startxref`-eltolást számol a `TRANSFER` heurisztikának.

Reprodukció: két szekciós fájl, a második xref stream → `rfind` = a korábbi `startxref`+5.

A javítás: közös `rfind_xref(start, end)` segédfüggvény a `re_xrefkw` mintával (`(?<![a-zA-Z])xref(?![a-zA-Z])`), amely az utolsó találatot adja; a három hely ezt hívja.

### 10.6 `self.startxref` a `base` korrekció előtti értéket tárolja – 852. sor

> **Javítva**: commit `fbf138b`. Teszt: `samples_new/06_junk_before_header.pdf`.

Ha a fejléc előtt szemét van és az offsetek a fejléchez relatívak (`base=hdr`), a `self.startxref[0]` a korrigálatlan `o`. A `check_transfer` ezt hasonlítja a `find_last_xref` abszolút pozíciójához, így `xdelta = hdr` hamis eltolás jön ki, ami befolyásolhatja a `TRANSFER` döntést (`xok`, `xsign`) és az üzenet `startxref off by N` részét.

Reprodukció: 8 byte szemét + helyes fájl → `base=8`, `startxref=(40, ...)`, `find_last_xref=48`.

A javítás: a `self.startxref` a `base` korrekció után, közvetlenül az xref feldolgozása előtt kap értéket.

### 10.7 Négyzetes idejű részek nagy, sérült fájlokon

> **Javítva**: commit `6c32c09` (`walk_xref` az `obj_starts()`-ból, `counts_upto`). Teszt: `samples_new/07_validate_bad_xref_offsets.pdf` és a `counts_upto` egységteszt.

- `walk_xref`, 941. sor: minden rossz xref-bejegyzésre külön teljes-fájl regex keresés (`badxref × fájlméret`). A már meglévő, gyorsítótárazott `obj_starts()` térkép ugyanezt adja egy menetben.
- `check_xref_shift`, 1325–1326. sor: minden sorra `d.count(b'\r\n', 0, P)` a fájl elejétől (`badxref × fájlméret`). A sorok pozíció szerint rendezettek, így a számlálás inkrementálisan, egy menetben elvégezhető.

Egy 50 MB-os, több ezer rossz bejegyzésű fájlnál ez percekig tarthatott. Nem helyességi hiba volt, de a "megengedő, mindent feldolgoz" célnak ellentmondott.

### 10.8 Kisebb észrevételek (lezárva)

- A nem használt `deflate_complete` és `import base64` törölve (commit `900aeb0`).
- Az objstm-fejléc sorrendje és a mély fejléc a 10.10–10.11-be került (javítva).
- **`analyze_obj` /Launch**: az `objs[i-2]` őrfeltételt kapott (commit `276030f`). Működési hibát nem okozott: az `analyze_obj` tokenlistája mindig `oid gen obj`-jal kezdődik, így az index legalább 2.
- **Kiírások mérete**: a közvetlen `/JS` string és az `embedded image` kiírása is 256 byte-ra vágva (commit `1f3c4fa`); a `content` teljes marad.
- **`os.listdir`** a parancssori indításban: nem létező útvonalnál kivétellel leáll. Nem javítjuk, csak tesztelésnél fut, jó paraméterrel.

### 10.9 LZW: `/EarlyChange 0` hamis „invalid code” hibák, hiányzó EOD kód hibaként – `LZWDecode`

> **Javítva**: commitok `a729b3d` (EarlyChange), `eaf0415` (EOD megjegyzés) és `2e3b28d` (csak biztos hossznál). Teszt: `samples_new/08_lzw_earlychange0_no_eod.pdf`.

A privát minták összehasonlító futásán derült ki (`err_lzw` könyvtár): egy fájl 5 LZW streamje mind `/EarlyChange 0`-val készült, és az író egyiknél sem írta ki a záró (EOD) kódot. A dekóder csak az EarlyChange=1 szélességváltást ismerte, ezért a 511./512. kód után két streamnél hamis „invalid code 1023 (dict size 511)” / „invalid code 936” hibát adott, és a kibontás a kép töredékénél megállt (10937 byte a 244280 helyett). A másik három streamnél a hiányzó EOD kód volt hiba, holott mind az 5 stream pontosan a kép méretére bomlik ki. A régi kód egyiküknél (obj 97) azért nem szólt, mert a hivatkozott `/Length` feloldása előtt a sorvége byte-ot is az adathoz vette, és annak első 5 bitje véletlenül kiegészítette a félbemaradt STOP kódot.

A javítás: a `LZWDecode` `early` paramétert kap a stream `/DecodeParms`-ából (a szélességváltás feltétele `dictlen + early >= 2^bits`). A buffer vége EOD nélkül `note` lesz a dekódolt mérettel, ha a stream vége biztos (`PDFStream.exact`: a `parse_pdf_obj` a `/Length` után megtalálta az `endstream`-et, tehát az író pont ennyit írt ki, csak az EOD-t hagyta el), és `error` marad, ha az `endstream` keresése döntött (rossz vagy feloldhatatlan `/Length`, hiányzó `endstream`), mert az csonkolásra is utalhat. A deflate hiányzó adler32-jével analóg döntés, a hossz-bizonyosság feltételével.

### 10.10 Objstm: az obj-határok a fejléc sorrendjéből – `parse_objstm`

> **Javítva**: commit `3d8fde8`. Teszt: `samples_new/09_objstm_unsorted_header.pdf`.

Az object stream fejlécének `oid offset` párjait a kód offset szerint növekvőnek feltételezte: a k-adik obj vége a k+1-edik pár offsetje volt. A specifikáció a sorrendet nem írja elő. Fordított sorrendnél a következő pár offsetje kisebb, ezért hamis „invalid offset” hiba lett és az obj kimaradt; nagyobb ugrásnál több obj olvadt egy tokenlistába, és az `analyze_obj` az egyik obj `/JS`-ét vagy `/Type /Page`-ét a másik számához kötötte. Kísérlet: ugyanaz a két oldal-obj a fejléc két sorrendjével 2, ill. 1 oldalt és egy hamis hibát adott.

A javítás: a határ a rákövetkező, offset szerint nagyobb obj kezdete (rendezett offsethalmaz), az utolsóé az adat vége.

### 10.11 A `%PDF` fejléc az első 1024 byte-on túl – `parse`, `scan_objs`

> **Javítva**: commit `1a47bea`. Teszt: `samples_new/10_deep_header_text_after_version.pdf`.

A fejlécet a kód az első 1024 byte után is megtalálja (`deep_header`, pl. RTFD csomagba ágyazott PDF), de a fejléc-sor további feldolgozásában három ciklus abszolút `p<1024` korláttal futott: a verzió utáni rész átugrása a sor végéig, a bináris komment átugrása, és a `scan_objs` elején az első `N G obj` keresése. 1024 utáni fejlécnél ezek eleve nem futottak le. Következmény: a `binheader` hamis lett (a TRANSFER-üzenet „binary header comment: NO” részét rontotta), `bad pdf header!` íródott ki, és ha a verzió után szöveg állt a sorban (`%PDF-1.5 www.opoosoft.com`), az objektumként értelmeződött: hamis „INVALID object type” hiba, amit ugyanez a fájl kevesebb szeméttel hiba nélkül átvészelt.

A javítás: a korlátok a fejléchez relatívak (`hdr+1024`, ill. a fejléc végétől 1024), 0 offsetű fejlécnél változatlan érték.

## 11. Tesztek (`samples_new/`)

A `samples_new/` könyvtár a 10.1–10.7 javítások regressziós tesztjeit tartalmazza: kis, kézzel összeállított PDF-eket, mindegyik egy-egy hibát céloz.

| Fájl | Mit tesztel |
|---|---|
| `00_clean_minimal.pdf` | hibátlan minimális PDF: 0 hiba, 1/1 oldal, 3 jó xref-bejegyzés |
| `01_objstm_last_token.pdf` | object stream, amelynek utolsó obj-a egy szám, és az adat pont ott végződik (10.1) |
| `02_startxref_no_value.pdf` | `startxref` után rögtön `%%EOF` (10.2): célzott hiba, az xref-et a tartalék megtalálja |
| `03_indirect_length_embedded_pdf.pdf` | tömörítetlen beágyazott PDF (benne `endstream`), hivatkozott `/Length` (10.3): teljes csatolmány, `inner.pdf` néven |
| `04a_attachments_filespec_first.pdf`, `04b_attachments_stream_first.pdf`, `04c_attachments_indirect_ef.pdf` | két csatolmány, a Filespec-ek a streamek előtt / után, ill. hivatkozott `/EF 8 0 R` dict-tel (10.4): `alpha.txt`/`beta.txt` helyes párosítás |
| `05_bad_startxref_xrefstream_prev_section.pdf` | ASCII xref-es első szekció + xref stream-es incremental update rossz `startxref`-fel (10.5): nincs hamis `invalid subsection header` |
| `06_junk_before_header.pdf` | 8 byte szemét a `%PDF` előtt (10.6): `base=8`, a `startxref` a korrigált érték |
| `07_validate_bad_xref_offsets.pdf` | validate mód, 2/4 xref-bejegyzés eltolva, a JS az egyik rossz offsetű obj-ban (10.7): a `walk_xref` mindet megtalálja |
| `09_objstm_unsorted_header.pdf` | object stream fordított sorrendű fejléccel (10.10): mindkét belső obj megvan, 3 oldal, 0 hiba |
| `10_deep_header_text_after_version.pdf` | 2000 byte szemét a `%PDF` előtt és szöveg a verzió után a fejléc-sorban (10.11): `base=2000`, `binheader` igaz, csak a JUNK hiba |
| `08_lzw_earlychange0_no_eod.pdf` (+ `08_payload.bin`) | három LZW csatolmány (10.9): `/EarlyChange 0` záró kód nélkül és alap EarlyChange záró kóddal (byte-ra egyeznek a payloaddal, a hiányzó EOD megjegyzés), valamint záró kód nélkül feloldhatatlan `/Length 99 0 R`-rel: ez az egyetlen hiba |

- `make_samples.py` – a fájlok determinisztikus (újra)generálása, helyes xref-táblával (van benne egy kis LZW-kódoló is); új minta ide kerül.
- `run_tests.py` – lefuttatja a mintákat és a hozzájuk tartozó elvárásokat, valamint néhány egységtesztet (lexer token a buffer végén, `counts_upto`, `rfind_xref`). Kilépési kód 0, ha minden rendben; `-v` kapcsolóval a sikeres ellenőrzéseket is kiírja.

```bash
python3 samples_new/run_tests.py -v
```

Az eredeti (`d993039`) változaton ugyanez a futás 12 ellenőrzésen bukik, a javított változaton mind átmegy.
