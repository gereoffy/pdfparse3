// SPDX-License-Identifier: MIT
// Copyright (c) 2026 A'rpi - part of pdfparse3
// AES-CBC (NIST SP 800-38A F.2) and RC4 (RFC 6229) known answers and buffer handling.
#include "pdfcrypt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void check(int ok, const char *what) {
    if (!ok) {
        fprintf(stderr, "FAIL: %s\n", what);
        exit(1);
    }
}

static size_t unhex(const char *s, uint8_t *out) {
    size_t n = strlen(s) / 2;
    for (size_t k = 0; k < n; k++) {
        unsigned b;
        check(sscanf(s + 2 * k, "%2x", &b) == 1, "hex test data");
        out[k] = (uint8_t)b;
    }
    return n;
}

static void knownAnswers(void) {
    static const char *keys[] = {
        "2b7e151628aed2a6abf7158809cf4f3c",
        "8e73b0f7da0e6452c810f32b809079e562f8ead2522c6b7b",
        "603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4",
    };
    static const char *ciphertexts[] = {
        "7649abac8119b246cee98e9b12e9197d5086cb9b507219ee95db113a917678b2"
        "73bed6b8e3c1743b7116e69e222295163ff1caa1681fac09120eca307586e1a7",
        "4f021db243bc633d7178183a9fa071e8b4d9ada9ad7dedf4e5e738763f69145a"
        "571b242012fb7ae07fa9baac3df102e008b0e27988598881d920a9e64f5615cd",
        "f58c4c04d6e5f1ba779eabfb5f7bfbd69cfc4e967edb808d679f777bc6702c7d"
        "39f23369a9d9bacfa530e26304231461b2eb05e2c39be9fcda6c19078c6a9d1b",
    };
    uint8_t key[32], plain[64], cipher[64], iv[16], initialIv[16];
    unhex("000102030405060708090a0b0c0d0e0f", initialIv);
    unhex("6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51"
          "30c81c46a35ce411e5fbc1191a0a52eff69f2445df4f9b17ad2b417be66c3710", plain);
    for (int k = 0; k < 3; k++) {
        pc_aes aes;
        check(pc_aes_init(&aes, key, unhex(keys[k], key)), "valid AES key length");
        unhex(ciphertexts[k], cipher);
        for (int offset = 0; offset < 16; offset++) {
            uint8_t in[80], out[80];
            memcpy(in + offset, plain, 64);
            memcpy(iv, initialIv, 16);
            pc_aes_encrypt_cbc(&aes, iv, in + offset, 64, out + offset);
            check(!memcmp(out + offset, cipher, 64), "NIST CBC encryption, all alignments");
            check(!memcmp(iv, cipher + 48, 16), "encryption final IV");
            memcpy(iv, initialIv, 16);
            pc_aes_decrypt_cbc(&aes, iv, out + offset, 64, out + offset);
            check(!memcmp(out + offset, plain, 64), "NIST in-place CBC decryption");
            check(!memcmp(iv, cipher + 48, 16), "decryption final IV");
            for (size_t split = 0; split <= 64; split += 16) {
                memcpy(out + offset, plain, 64);
                memcpy(iv, initialIv, 16);
                pc_aes_encrypt_cbc(&aes, iv, out + offset, split, out + offset);
                pc_aes_encrypt_cbc(&aes, iv, out + offset + split, 64 - split, out + offset + split);
                check(!memcmp(out + offset, cipher, 64), "split in-place encryption");
                check(!memcmp(iv, cipher + 48, 16), "split encryption final IV");
                memcpy(iv, initialIv, 16);
                pc_aes_decrypt_cbc(&aes, iv, cipher, split, out + offset);
                pc_aes_decrypt_cbc(&aes, iv, cipher + split, 64 - split, out + offset + split);
                check(!memcmp(out + offset, plain, 64), "split out-of-place decryption");
                check(!memcmp(iv, cipher + 48, 16), "split decryption final IV");
            }
        }
    }
    pc_aes aes;
    for (size_t n = 0; n <= 33; n++)
        if (n != 16 && n != 24 && n != 32) check(!pc_aes_init(&aes, key, n), "reject invalid AES key length");
}

static uint32_t randomState = 0x12345678;
static uint8_t randomByte(void) {
    randomState ^= randomState << 13;
    randomState ^= randomState >> 17;
    randomState ^= randomState << 5;
    return (uint8_t)randomState;
}

static void roundTrips(void) {
    uint8_t key[32], initialIv[16], iv[16], plain[4096], cipher[4097], out[4097];
    for (int rep = 0; rep < 32; rep++) {
        for (size_t i = 0; i < sizeof key; i++) key[i] = randomByte();
        for (size_t i = 0; i < sizeof initialIv; i++) initialIv[i] = randomByte();
        for (size_t i = 0; i < sizeof plain; i++) plain[i] = randomByte();
        for (size_t kl = 16; kl <= 32; kl += 8) {
            pc_aes aes;
            check(pc_aes_init(&aes, key, kl), "random key");
            for (size_t n = 16; n <= sizeof plain; n *= 2) {
                memcpy(iv, initialIv, 16);
                pc_aes_encrypt_cbc(&aes, iv, plain, n, cipher + 1);
                memcpy(iv, initialIv, 16);
                pc_aes_decrypt_cbc(&aes, iv, cipher + 1, n, out + 1);
                check(!memcmp(plain, out + 1, n), "random CBC round trip");
                check(!memcmp(iv, cipher + 1 + n - 16, 16), "random CBC final IV");
                memcpy(out + 1, cipher + 1, n);
                memcpy(iv, initialIv, 16);
                for (size_t o = 0; o < n; o += 16) pc_aes_decrypt_cbc(&aes, iv, out + 1 + o, 16, out + 1 + o);
                check(!memcmp(plain, out + 1, n), "random in-place blockwise CBC decryption");
            }
        }
    }
}

static void rc4KnownAnswers(void) {
    static const char *keys[] = {"0102030405", "01020304050607", "0102030405060708090a0b0c0d0e0f10"};
    static const char *first[] = {
        "b2396305f03dc027ccc3524a0a1118a86982944f18fc82d589c403a47a0d0919",
        "293f02d47f37c9b633f2af5285feb46be620f1390d19bd84e2e0fd752031afc1",
        "9ac7cc9a609d1ef7b2932899cde41b975248c4959014126a6e8a84f11d1a9e1c",
    };
    static const char *last[] = {
        "ff25b58995996707e51fbdf08b34d875", "e74b0b9731227fd37c0ec08a47ddd8b8", "a36a4c301ae8ac13610ccbc12256cacc",
    };
    uint8_t key[32], expected[32], zero[4112] = {0}, out[4113];
    for (int k = 0; k < 3; k++) {
        size_t kl = unhex(keys[k], key);
        pc_rc4(key, kl, zero, sizeof zero, out + 1);
        unhex(first[k], expected);
        check(!memcmp(out + 1, expected, 32), "RFC 6229 RC4 first 32 bytes");
        unhex(last[k], expected);
        check(!memcmp(out + 1 + 4096, expected, 16), "RFC 6229 RC4 offset 4096");
        unhex(first[k], expected);
        for (size_t n = 0; n <= 32; n++) {
            memset(out, 0xa5, sizeof out);
            memset(out + 1, 0, n);
            pc_rc4(key, kl, out + 1, n, out + 1);
            check(!memcmp(out + 1, expected, n), "RC4 in-place, short and empty buffers");
            check(out[0] == 0xa5 && out[n + 1] == 0xa5, "RC4 buffer bounds");
        }
    }
}

int main(void) {
    knownAnswers();
    roundTrips();
    puts("AES-CBC: NIST AES-128/192/256, alignment, in-place, split calls and IVs OK");
    rc4KnownAnswers();
    puts("RC4: RFC 6229, short buffers and in-place OK");
    return 0;
}
