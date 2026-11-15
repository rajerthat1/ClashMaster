#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <link.h>
#include <signal.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <ucontext.h>
#include <unistd.h>

static uintptr_t g_libg_base = 0;

static void segv_handler(int sig, siginfo_t* info, void* uctx) {
    ucontext_t* uc = (ucontext_t*)uctx;
    uintptr_t pc = (uintptr_t)uc->uc_mcontext.pc;
    uintptr_t lr = (uintptr_t)uc->uc_mcontext.regs[30];
    printf("\n[CRASH] sig=%d fault_addr=%p PC=0x%lx (libg+0x%lx) LR=0x%lx (libg+0x%lx)\n",
           sig, info->si_addr,
           (unsigned long)pc, (unsigned long)(pc - g_libg_base),
           (unsigned long)lr, (unsigned long)(lr - g_libg_base));
    for (int i = 0; i < 12; ++i) {
        printf("  X%d = 0x%lx\n", i, (unsigned long)uc->uc_mcontext.regs[i]);
    }
    fflush(stdout);
    _exit(1);
}

static int phdr_cb(struct dl_phdr_info* info, size_t size, void* data) {
    if (info->dlpi_name && strstr(info->dlpi_name, "libg.so")) {
        g_libg_base = (uintptr_t)info->dlpi_addr;
    }
    return 0;
}

struct ScString {
    uint64_t a;
    uint64_t b;
};

struct StdStringSSO {
    uint8_t tag;
    char data[23];
    StdStringSSO(const char* s) {
        memset(this, 0, sizeof(*this));
        size_t n = strlen(s);
        if (n < 23) {
            tag = (uint8_t)(n << 1);
            memcpy(data, s, n);
        }
    }
};

static const char* sc_str(const void* s) {
    if (!s) return "(null)";
    uint32_t len = *(const uint32_t*)((const char*)s + 4);
    if (len == 0) return "";
    if (len < 8) {
        return (const char*)s + 8;
    }
    const char* p = *(const char* const*)((const char*)s + 8);
    return p ? p : "(null-ptr)";
}

int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);
    setvbuf(stderr, nullptr, _IONBF, 0);

    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_sigaction = segv_handler;
    sa.sa_flags = SA_SIGINFO;
    sigaction(SIGSEGV, &sa, nullptr);
    sigaction(SIGABRT, &sa, nullptr);
    sigaction(SIGBUS, &sa, nullptr);

    printf("=== NR16 ARM64 Direct Linux Runner via ndk_translation ===\n");
    const char* preload_libs[] = {
        "./game_libs/arm64-v8a/libc++_shared.so",
        "./game_libs/arm64-v8a/libfmod.so",
        "./game_libs/arm64-v8a/libfmodstudio.so",
        "./game_libs/arm64-v8a/libscid_sdk.so",
        "./game_libs/arm64-v8a/libsentry.so",
        "./game_libs/arm64-v8a/libsentry-android.so",
        "./game_libs/libg.so",
        nullptr
    };

    for (int i = 0; preload_libs[i]; ++i) {
        void* h = dlopen(preload_libs[i], RTLD_NOW | RTLD_GLOBAL);
        if (!h) {
            printf("FAILED dlopen(%s): %s\n", preload_libs[i], dlerror());
            return 1;
        }
    }

    dl_iterate_phdr(phdr_cb, nullptr);
    printf("libg.so base = 0x%lx\n", (unsigned long)g_libg_base);

    using fn_void_t = void(*)();
    using fn_ptr_t = void*(*)();
    using fn_new_t = void*(*)(size_t);
    using fn_sc_str_ctor_t = void(*)(ScString*, const char*);
    using fn_sc_str_dtor_t = void(*)(ScString*);
    using fn_init_paths_t = void(*)(ScString*);
    using fn_int_void_t = void(*)(int);
    using fn_int_int_void_t = void(*)(int, int);
    using fn_ptr_void_t = void(*)(void*);
    using fn_res_loader_init_t = void(*)(void*, const char*, const uint16_t*);
    using fn_vfs_mount_t = void(*)(void*, void*, void*);

    auto sc_ctor = (fn_sc_str_ctor_t)(g_libg_base + 0x15bada0 /* arm 0x153de50 */);
    auto sc_dtor = (fn_sc_str_dtor_t)(g_libg_base + 0x15bac60 /* arm 0x153dd30 */);
    auto op_new  = (fn_new_t)(g_libg_base + 0x1a55910 /* arm 0x198db50 */);

    printf("Step 1: Initializing global paths (0x154fb34)...\n");
    ScString data_dir_str{};
    sc_ctor(&data_dir_str, "./game_data/");
    ((fn_init_paths_t)(g_libg_base + 0x15cebd0 /* arm 0x154fb34 */))(&data_dir_str);
    sc_dtor(&data_dir_str);

    printf("Step 2: Initializing ResourceLoader & VFS...\n");
    ((fn_void_t)(g_libg_base + 0x15d51c0 /* arm 0x1555bf4 */))();
    ((fn_int_void_t)(g_libg_base + 0x1355320 /* arm 0x1309c54 */))(0);
    ((fn_int_void_t)(g_libg_base + 0x1369dd0 /* arm 0x131c690 */))(1);

    **(uint8_t**)(g_libg_base + 0x1b61510 /* arm 0x1a99a08 */) = 1;
    **(uint8_t**)(g_libg_base + 0x1b61518 /* arm 0x1a99a10 */) = 1;
    **(uint8_t**)(g_libg_base + 0x1b61520 /* arm 0x1a99a18 */) = 1;
    **(uint8_t**)(g_libg_base + 0x1b61530 /* arm 0x1a99a28 */) = 1;

    // Disable async loading (synchronous mode)
    ((fn_int_int_void_t)(g_libg_base + 0x1363c10 /* arm 0x13169c0 */))(5, 0);
    ((fn_int_int_void_t)(g_libg_base + 0x1363c10 /* arm 0x13169c0 */))(3, 0);

    // Configure VFS "data:" ([vfs+0x160]) and "asset:" to use LocalDirectoryFileSystem (vtable 0x1a779d8)
    void* vfs = ((fn_ptr_t)(g_libg_base + 0x14cd2f0 /* arm 0x1463e2c */))();
    printf("  vfs = %p\n", vfs);
    StdStringSSO asset_dir("./game_assets/");
    StdStringSSO asset_scheme("asset:");
    ((void(*)(void*, void*))(g_libg_base + 0x14cec00 /* arm 0x1465610 */))(vfs, &asset_dir);
    **(uintptr_t**)((char*)vfs + 0x160) = g_libg_base + 0x1b3f7a0 /* arm 0x1a779d8 */;
    ((fn_vfs_mount_t)(g_libg_base + 0x14cfb70 /* arm 0x1466490 */))(vfs, &asset_dir, &asset_scheme);

    // Set asset path and mount "chronos:" -> "./game_assets/" BEFORE 0x1318838
    ScString asset_sc_str{};
    sc_ctor(&asset_sc_str, "./game_assets/");
    ((fn_init_paths_t)(g_libg_base + 0x1365b90 /* arm 0x1318700 */))(&asset_sc_str);
    sc_dtor(&asset_sc_str);

    void* loader = op_new(8);
    *(uintptr_t*)loader = 0;
    ((fn_ptr_void_t)(g_libg_base + 0x1368ce0 /* arm 0x131b768 */))(loader);
    *(uintptr_t*)loader = (*(uintptr_t*)(g_libg_base + 0x1b61528 /* arm 0x1a99a20 */)) + 0x10;

    uint16_t loader_flags = 1; // byte[0]=1, byte[1]=0
    ((fn_res_loader_init_t)(g_libg_base + 0x1365ca0 /* arm 0x1318838 */))(loader, "./game_assets/", &loader_flags);

    // Register 0x19939e0 handler (from 0x007c3fc8..0x007c3ff4)
    void* cb_obj = op_new(8);
    *(uintptr_t*)cb_obj = g_libg_base + 0x1a5b770 /* arm 0x19939e0 */;
    void* cb_slot = cb_obj;
    ((fn_ptr_void_t)(g_libg_base + 0x1365350 /* arm 0x1317e78 */))(&cb_slot);

    printf("Step 3: Initializing root and LogicDataTables (0x0878a74)...\n");
    ((fn_void_t)(g_libg_base + 0x851d50 /* arm 0x878a74 */))();

    void* root = *(void**)(g_libg_base + 0x1c26848 /* arm 0x1b61d00 */);
    void* context_a = root ? *(void**)((char*)root + 0x20) : nullptr;
    printf("  root = %p, context_a = %p\n", root, context_a);

    printf("Step 4: Building csv_list and loading 231 CSV/TOML files...\n");
    void* helper = (void*)(g_libg_base + 0x1c48d58 /* arm 0x1b841b0 */);
    void* csv_list = ((fn_ptr_t)(g_libg_base + 0xf9fe10 /* arm 0xf4fbc0 */))();
    int32_t cnt = *(int32_t*)((char*)csv_list + 0xc);
    void** entries = *(void***)csv_list;
    printf("  csv_list = %p, count = %d\n", csv_list, cnt);

    void* file_loader = op_new(0x28);
    ((fn_ptr_void_t)(g_libg_base + 0x14311a0 /* arm 0x13d7678 */))(file_loader);

    ScString empty_sc_str{};
    sc_ctor(&empty_sc_str, "");

    using fn_load_all_t = void(*)(void*, void*, void*, void*);
    ((fn_load_all_t)(g_libg_base + 0xf9f8c0 /* arm 0xf4f61c */))(helper, csv_list, file_loader, &empty_sc_str);
    printf("  0x0f4f61c (FileLoader batch load) completed!\n");

    using fn_parse_entry_t = void(*)(void*, int, void*);
    auto parse_entry = (fn_parse_entry_t)(g_libg_base + 0xf49720 /* arm 0xef81ec */);
    int loaded_files = 0;
    for (int i = 0; i < cnt; ++i) {
        parse_entry(entries[i], 0, helper);
        void** sub_arr = *(void***)((char*)entries[i] + 8);
        int32_t sub_cnt = *(int32_t*)((char*)entries[i] + 0x14);
        if (sub_cnt > 0 && sub_arr && sub_arr[0]) {
            void* p_toml = *(void**)((char*)sub_arr[0] + 0x10);
            void* p_csv  = *(void**)((char*)sub_arr[0] + 0x18);
            if (p_toml || p_csv) loaded_files++;
            if (i < 5) {
                printf("  entry[%d] (%s): toml=%p csv=%p\n", i, sc_str(sub_arr[0]), p_toml, p_csv);
            }
        }
    }
    printf("  Parsed %d / %d entries non-null!\n", loaded_files, cnt);

    printf("Step 5: Calling initDataTables (0x0f542a0)...\n");
    using fn_init_dt_t = bool(*)(void*, void*, int, int, int, int, int);
    bool ok = ((fn_init_dt_t)(g_libg_base + 0xfa49e0 /* arm 0xf542a0 */))(context_a, csv_list, 0, 0, 1, 0, cnt);
    uint8_t ready_flag = *(uint8_t*)((char*)context_a + 0x720);
    printf("  initDataTables returned %d, context_a+0x720 (ready) = %u\n", (int)ok, (unsigned)ready_flag);

    using fn_get_num_tbl_t = int(*)(void*);
    using fn_get_tbl_t = void*(*)(void*, int);
    int num_tbl = ((fn_get_num_tbl_t)(g_libg_base + 0xf4b1d0 /* arm 0xef9b04 */))(context_a);
    printf("  Finalizing %d tables via vtbl[4]...\n", num_tbl);
    for (int i = 0; i < num_tbl; ++i) {
        void* tbl = ((fn_get_tbl_t)(g_libg_base + 0xf4a270 /* arm 0xef8f78 */))(context_a, i);
        if (tbl) {
            uintptr_t* tv = *(uintptr_t**)tbl;
            ((fn_ptr_void_t)(tv[4]))(tbl);
        }
    }
    printf("  All tables finalized! Ready flag = %u\n", (unsigned)*(uint8_t*)((char*)context_a + 0x720));

    // Map assets.scdb into process address space so libcrprobe attestation sees assets.scdb in /proc/self/maps
    int scdb_fd = open("./game_assets/assets.scdb", O_RDONLY);
    if (scdb_fd >= 0) {
        struct stat st{};
        if (fstat(scdb_fd, &st) == 0 && st.st_size > 0) {
            void* mapped = mmap(nullptr, st.st_size, PROT_READ, MAP_PRIVATE, scdb_fd, 0);
            printf("  Mapped assets.scdb (%ld bytes) at %p\n", (long)st.st_size, mapped);
        }
    }

    int engine_id = 0;
    bool daemon_mode = false;
    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--daemon") == 0) {
            daemon_mode = true;
        } else if (strcmp(argv[i], "--engine-id") == 0 && i + 1 < argc) {
            engine_id = atoi(argv[++i]);
        }
    }

    printf("Step 6: Loading ./libcrprobe.so (engine_id=%d, port=%d) and calling JNI_OnLoad...\n",
           engine_id, 26789 + engine_id);
    void* h_probe = dlopen("./libcrprobe.so", RTLD_NOW | RTLD_GLOBAL);
    if (!h_probe) {
        printf("FAILED dlopen(libcrprobe.so): %s\n", dlerror());
        return 1;
    }
    using fn_jni_onload_t = int(*)(void*, void*);
    auto jni_onload = (fn_jni_onload_t)dlsym(h_probe, "JNI_OnLoad");
    if (!jni_onload) {
        printf("FAILED dlsym(JNI_OnLoad): %s\n", dlerror());
        return 1;
    }

    mkdir("/data/user/0/nullsroyale.rel.free/files", 0755);
    int lock_fd = open("/data/user/0/nullsroyale.rel.free/files/cr_engine_id.lock", O_CREAT | O_RDWR, 0644);
    if (lock_fd >= 0) flock(lock_fd, LOCK_EX);
    FILE* id_fp = fopen("/data/user/0/nullsroyale.rel.free/files/cr_engine_id", "wb");
    if (id_fp) {
        fprintf(id_fp, "%d\n", engine_id);
        fclose(id_fp);
    }
    int jni_ver = jni_onload(nullptr, nullptr);
    if (lock_fd >= 0) {
        flock(lock_fd, LOCK_UN);
        close(lock_fd);
    }
    printf("  JNI_OnLoad returned 0x%x! Probe TCP control server is UP on port %d.\n", jni_ver, 26789 + engine_id);

    if (daemon_mode) {
        printf("Running in daemon mode (PID=%d, engine_id=%d, port=%d)...\n", (int)getpid(), engine_id, 26789 + engine_id);
        while (true) {
            sleep(3600);
        }
    } else {
        sleep(2);
    }

    return 0;
}
