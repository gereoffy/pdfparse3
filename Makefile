# pdfparse3.cpp (the C++ port of pdfparse3.py's object layer): its test tool and the comparison with the python
#   make            pdfdump: pdfparse3.cpp's view of a PDF (the objects, the streams' decoded size and MD5, the pages)
#   make asan       pdfdump-asan: the same with the address and undefined behaviour sanitizers
#   make compare    pdfdump against pdfdump.py (pdfparse3.py) on samples/ and samples_new/, the diffs into OUT
#   make test       samples_new/run_tests.py (pdfparse3.py's tests, and pdfparse3.cpp's when pdfdump is built)
#   make test-crypt AES-CBC and RC4 known answers, native and forced portable AES backends
CC  ?= cc
CXX ?= c++
OUT ?= /tmp/pdfcompare
SRC := pdfdump.cpp pdfparse3.cpp pdfparse3.h pdfcrypt.c pdfcrypt.h

pdfdump: $(SRC)
	$(CC) -std=c99 -O2 -c pdfcrypt.c -o pdfcrypt.o
	$(CXX) -std=c++17 -O2 -Wall -Wextra pdfdump.cpp pdfparse3.cpp pdfcrypt.o -lz -o $@

asan: pdfdump-asan
pdfdump-asan: $(SRC)
	$(CC) -std=c99 -O1 -g -fsanitize=address,undefined -c pdfcrypt.c -o pdfcrypt-asan.o
	$(CXX) -std=c++17 -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer pdfdump.cpp pdfparse3.cpp pdfcrypt-asan.o -lz -o $@

compare: pdfdump
	./pdfcompare.py $(OUT) samples/*.pdf samples_new/*.pdf

pdfcrypt-test: pdfcrypt_test.c pdfcrypt.c pdfcrypt.h
	$(CC) -std=c99 -O2 -Wall -Wextra pdfcrypt_test.c pdfcrypt.c -o $@

pdfcrypt-test-soft: pdfcrypt_test.c pdfcrypt.c pdfcrypt.h
	$(CC) -std=c99 -O2 -Wall -Wextra -DPC_AES_HARDWARE=0 pdfcrypt_test.c pdfcrypt.c -o $@

test-crypt: pdfcrypt-test pdfcrypt-test-soft
	./pdfcrypt-test
	./pdfcrypt-test-soft

test: pdfdump test-crypt
	python3 samples_new/run_tests.py

.PHONY: asan compare test test-crypt
