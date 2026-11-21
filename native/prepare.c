/* SPDX-FileCopyrightText: 2026 Carlos Diaz
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Read-only transaction preparation. There is deliberately no commit call.
 */
#include <alpm.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void string(const char *s) {
    putchar('"');
    if(s) for(const unsigned char *p = (const unsigned char *)s; *p; p++) {
        if(*p == '"' || *p == '\\') { putchar('\\'); putchar(*p); }
        else if(*p < 32) printf("\\u%04x", *p);
        else putchar(*p);
    }
    putchar('"');
}

int main(int argc, char **argv) {
    if(argc < 5) {
        fprintf(stderr, "usage: prepare ROOT SHADOW_DB ARCH [--repo NAME|--ignore NAME|--ignore-group NAME|--package FILE]...\n");
        return 2;
    }
    alpm_errno_t error;
    alpm_handle_t *h = alpm_initialize(argv[1], argv[2], &error);
    if(!h) { fprintf(stderr, "%s\n", alpm_strerror(error)); return 2; }
    alpm_option_add_architecture(h, argv[3]);
    int rc = 2, initialized = 0;
    for(int i = 4; i < argc; i += 2) {
        if(i + 1 == argc) goto cleanup;
        if(!strcmp(argv[i], "--repo")) {
            alpm_db_t *db = alpm_register_syncdb(h, argv[i + 1], 0);
            if(!db || alpm_db_set_usage(db, ALPM_DB_USAGE_ALL)) goto cleanup;
        } else if(!strcmp(argv[i], "--ignore")) alpm_option_add_ignorepkg(h, argv[i + 1]);
        else if(!strcmp(argv[i], "--ignore-group")) alpm_option_add_ignoregroup(h, argv[i + 1]);
        else if(strcmp(argv[i], "--package")) goto cleanup;
    }
    if(alpm_trans_init(h, 0)) goto cleanup;
    initialized = 1;
    /* Explicit candidates take precedence over the sync upgrade's same-name targets. */
    for(int i = 4; i < argc; i += 2) if(!strcmp(argv[i], "--package")) {
        alpm_pkg_t *pkg = NULL;
        if(alpm_pkg_load(h, argv[i + 1], 1, 0, &pkg)) goto cleanup;
        if(alpm_add_pkg(h, pkg)) { alpm_pkg_free(pkg); goto cleanup; }
    }
    if(alpm_sync_sysupgrade(h, 0)) goto cleanup;
    alpm_list_t *data = NULL;
    if(alpm_trans_prepare(h, &data)) {
        error = alpm_errno(h);
        printf("{\"compatible\":false,\"error\":"); string(alpm_strerror(error));
        printf(",\"missing\":[");
        if(error == ALPM_ERR_UNSATISFIED_DEPS) {
            int first = 1;
            for(alpm_list_t *item = data; item; item = item->next) {
                alpm_depmissing_t *missing = item->data;
                char *dep = alpm_dep_compute_string(missing->depend);
                printf("%s{\"package\":", first ? "" : ","); string(missing->target);
                printf(",\"dependency\":"); string(dep);
                printf(",\"causing_package\":"); string(missing->causingpkg);
                putchar('}'); first = 0; free(dep);
                alpm_depmissing_free(missing);
            }
        }
        alpm_list_free(data);
        puts("]}"); rc = 20;
    } else {
        printf("{\"compatible\":true,\"transaction\":[");
        int first = 1;
        for(alpm_list_t *item = alpm_trans_get_add(h); item; item = item->next) {
            alpm_pkg_t *pkg = item->data;
            printf("%s{\"package\":", first ? "" : ","); string(alpm_pkg_get_name(pkg));
            printf(",\"version\":"); string(alpm_pkg_get_version(pkg));
            putchar('}'); first = 0;
        }
        puts("]}"); rc = 0;
    }
cleanup:
    if(rc == 2) fprintf(stderr, "native preparation failed: %s\n", alpm_strerror(alpm_errno(h)));
    if(initialized) alpm_trans_release(h);
    alpm_release(h);
    return rc;
}
