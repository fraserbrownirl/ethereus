// Host ASan/UBSan harness for the eth firmware sources. Modes:
//   fuzz cmd <cmdline...>     run handle_cmd, print captured output
//   fuzz decode <types> <hex>  run abi_decode, print rc/err/out
// Any sanitizer finding aborts nonzero; clean errors print and exit 0.
#include <stdio.h>
#include <string.h>
#include "abi.h"

char host_out[1 << 20];
size_t host_out_len;
int host_cmd(const char *cmd);

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: fuzz cmd|decode ...\n"); return 2; }
    if (strcmp(argv[1], "cmd") == 0) {
        if (argc < 3) return 2;
        char line[65536]; size_t p = 0;
        for (int i = 2; i < argc; i++) {
            size_t l = strlen(argv[i]);
            if (p + l + 1 >= sizeof(line)) return 2;
            memcpy(line + p, argv[i], l); p += l;
            line[p++] = (i + 1 < argc) ? ' ' : 0;
        }
        host_out_len = 0;
        host_cmd(line);
        host_out[host_out_len] = 0;
        fputs(host_out, stdout);
        return 0;
    }
    if (strcmp(argv[1], "decode") == 0) {
        if (argc != 4) return 2;
        char out[4096], err[128];
        int rc = abi_decode(argv[2], argv[3], out, sizeof(out), err, sizeof(err));
        printf("rc=%d err=%s out=%s", rc, err, rc ? "(none)\n" : out);
        return 0;
    }
    return 2;
}
