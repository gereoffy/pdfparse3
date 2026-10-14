# Szintetikus minták és tesztek

A `samples/*.pdf` mintákat a `gen_samples.py` állítja elő; minden minta egy-egy trükkös vagy sérült esetet tesztel
(a generátorban a minta felett rövid leírás). A minták bájtra reprodukálhatók: a generátor újrafuttatása után a
`git status` nem mutathat változást.

| Fájl | Mire való |
|---|---|
| `gen_samples.py` | a minták generálása ide (`python3 samples/gen_samples.py`). A tesztek a mintákat memóriában generálják, a fájlok kézi vizsgálathoz és a többi eszközhöz kellenek |
| `test_samples.py` | a minták elvárt eredményei: pontszám content és validate módban, kinyert tartalom (JS, csatolmány, URL-lista, `javascript:` URI), diagnózisok (TRANSFER, TRUNCATED, JUNK, GAP, CORRUPT, ENCODED), a sorvége-visszaalakítás, a titkosítás visszafejtése és a `pdf2str` szövege (`TEXT` tábla). `-v`: mintánként kiírja |
| `test_unit.py` | egységtesztek: lexer (string escape-ek, hexa string, `#xx` nevek), dekóderek (zlib az adler32-esetekkel, LZW, ASCII85, ASCIIHex, RunLength, predictor), AES / RC4 tesztvektorok, `objs_tree` / `content_ops` / `get_obj`, a `pdf2str` robusztussága |

Futtatás a repó gyökeréből (mind sima `python3`-mal vagy pypy-vel, `pytest`-tel is):

```
python3 samples/test_unit.py
python3 samples/test_samples.py
python3 samples_new/run_tests.py
```

Új trükkös vagy sérült eset: új minta a `gen_samples.py`-ba, az elvárt eredmény a `test_samples.py` `EXPECTED` táblájába (és ha van, a diagnózis a `DIAG`, a szöveg a `TEXT` táblába), majd `python3 samples/gen_samples.py`.
