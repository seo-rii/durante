/* Test-only, bounded single-thread location diagnostics. The normal client
 * never compiles this code and exposes no profiling option. */
#include <elf.h>
#define PROFILE_SLOTS 131072U
#define PROFILE_ENTRIES 65536U
#define PROFILE_MODULES 128U
#define PROFILE_FILE_BYTES (1024U * 1024U - 256U)
typedef struct {
  app_pc pc;
  uint64_t hits;
  uint32_t instructions;
} profile_entry_t;
typedef struct {
  app_pc start, end;
  char name[128], build_id[129];
} profile_module_t;
static profile_entry_t profile_entries[PROFILE_SLOTS];
static profile_module_t profile_modules[PROFILE_MODULES];
static uint64_t profile_charge, profile_rollback_bytes, profile_replays;
static uint64_t profile_module_unloads;
static unsigned profile_used, profile_module_count;
static thread_id_t profile_thread;
static int profile_failed;

/* Identities are resolved at exit. Reject any intervening unload, even when
 * its address was later reused by a module with a readable build-id. */
static void profile_module_unload(void *drcontext, const module_data_t *info) {
  (void)drcontext;
  (void)info;
  __atomic_add_fetch(&profile_module_unloads, 1, __ATOMIC_RELAXED);
  __atomic_store_n(&profile_failed, 1, __ATOMIC_RELAXED);
}

static void profile_count(app_pc pc, uint64_t instructions) {
  thread_id_t tid = dr_get_thread_id(dr_get_current_drcontext());
  thread_id_t empty = 0;
  __atomic_compare_exchange_n(&profile_thread, &empty, tid, false,
                              __ATOMIC_RELAXED, __ATOMIC_RELAXED);
  if (__atomic_load_n(&profile_thread, __ATOMIC_RELAXED) != tid || !pc ||
      instructions > UINT32_MAX ||
      UINT64_MAX - profile_charge < instructions) {
    __atomic_store_n(&profile_failed, 1, __ATOMIC_RELAXED);
    return;
  }
  unsigned slot = (unsigned)(((ptr_uint_t)pc >> 1) ^ instructions) &
                  (PROFILE_SLOTS - 1);
  for (unsigned attempt = 0; attempt < PROFILE_SLOTS; ++attempt) {
    profile_entry_t *entry = &profile_entries[slot];
    if (!entry->pc) {
      if (profile_used == PROFILE_ENTRIES) {
        __atomic_store_n(&profile_failed, 1, __ATOMIC_RELAXED);
        return;
      }
      entry->pc = pc;
      entry->instructions = (uint32_t)instructions;
      ++profile_used;
    }
    if (entry->pc == pc && entry->instructions == instructions) {
      if (entry->hits == UINT64_MAX) {
        __atomic_store_n(&profile_failed, 1, __ATOMIC_RELAXED);
        return;
      }
      ++entry->hits;
      profile_charge += instructions;
      return;
    }
    slot = (slot + 1) & (PROFILE_SLOTS - 1);
  }
  __atomic_store_n(&profile_failed, 1, __ATOMIC_RELAXED);
}

static void profile_replay(uint64_t rollback) {
  if (__atomic_load_n(&profile_thread, __ATOMIC_RELAXED) !=
      dr_get_thread_id(dr_get_current_drcontext())) {
    __atomic_store_n(&profile_failed, 1, __ATOMIC_RELAXED);
    return;
  }
  ++profile_replays;
  if (UINT64_MAX - profile_rollback_bytes < rollback)
    __atomic_store_n(&profile_failed, 1, __ATOMIC_RELAXED);
  else
    profile_rollback_bytes += rollback;
}

/* Read only the loaded ELF header/program headers and its bounded GNU note.
 * This also identifies the vDSO, which has no ordinary filesystem path. */
static bool profile_build_id(app_pc start, char *output) {
  Elf64_Ehdr header;
  if (!dr_safe_read(start, sizeof(header), &header, NULL) ||
      memcmp(header.e_ident, ELFMAG, SELFMAG) ||
      header.e_ident[EI_CLASS] != ELFCLASS64 ||
      header.e_ident[EI_DATA] != ELFDATA2LSB ||
      header.e_phnum > 128 || header.e_phentsize != sizeof(Elf64_Phdr) ||
      header.e_phoff > 65536)
    return false;
  Elf64_Addr minimum = UINT64_MAX;
  for (unsigned i = 0; i < header.e_phnum; ++i) {
    Elf64_Phdr ph;
    if (!dr_safe_read(start + header.e_phoff + i * sizeof(ph),
                      sizeof(ph), &ph, NULL))
      return false;
    if (ph.p_type == PT_LOAD && ph.p_vaddr < minimum)
      minimum = ph.p_vaddr;
  }
  if (minimum == UINT64_MAX)
    return false;
  ptr_uint_t bias = (ptr_uint_t)start - (minimum & ~(ptr_uint_t)4095);
  for (unsigned i = 0; i < header.e_phnum; ++i) {
    Elf64_Phdr ph;
    if (!dr_safe_read(start + header.e_phoff + i * sizeof(ph),
                      sizeof(ph), &ph, NULL))
      return false;
    if (ph.p_type != PT_NOTE || ph.p_filesz > 65536)
      continue;
    size_t offset = 0;
    for (unsigned note = 0; note < 128 && offset + sizeof(Elf64_Nhdr) <= ph.p_filesz;
         ++note) {
      Elf64_Nhdr nh;
      app_pc address = (app_pc)(bias + ph.p_vaddr + offset);
      if (!dr_safe_read(address, sizeof(nh), &nh, NULL))
        return false;
      size_t name_size = ((size_t)nh.n_namesz + 3) & ~(size_t)3;
      size_t desc_size = ((size_t)nh.n_descsz + 3) & ~(size_t)3;
      if (name_size > 65536 || desc_size > 65536 ||
          sizeof(nh) + name_size + desc_size > ph.p_filesz - offset)
        return false;
      if (nh.n_type == NT_GNU_BUILD_ID && nh.n_namesz == 4 &&
          nh.n_descsz > 0 && nh.n_descsz <= 64) {
        char name[4];
        unsigned char value[64];
        if (!dr_safe_read(address + sizeof(nh), 4, name, NULL) ||
            memcmp(name, "GNU\0", 4) ||
            !dr_safe_read(address + sizeof(nh) + name_size, nh.n_descsz,
                          value, NULL))
          return false;
        const char hex[] = "0123456789abcdef";
        for (unsigned j = 0; j < nh.n_descsz; ++j) {
          output[2 * j] = hex[value[j] >> 4];
          output[2 * j + 1] = hex[value[j] & 15];
        }
        output[2 * nh.n_descsz] = 0;
        return true;
      }
      offset += sizeof(nh) + name_size + desc_size;
    }
  }
  return false;
}

static int profile_module(app_pc pc) {
  module_data_t *module = dr_lookup_module(pc);
  if (!module)
    return -1;
  for (unsigned i = 0; i < profile_module_count; ++i) {
    if (profile_modules[i].start == module->start &&
        profile_modules[i].end == module->end) {
      dr_free_module_data(module);
      return (int)i;
    }
  }
  if (profile_module_count == PROFILE_MODULES) {
    dr_free_module_data(module);
    return -1;
  }
  unsigned index = profile_module_count++;
  profile_module_t *record = &profile_modules[index];
  record->start = module->start;
  record->end = module->end;
  const char *name = dr_module_preferred_name(module);
  dr_snprintf(record->name, sizeof(record->name), "%s", name ? name : "unnamed");
  record->name[sizeof(record->name) - 1] = 0;
  for (char *c = record->name; *c; ++c)
    if (*c == '\t' || *c == '\r' || *c == '\n')
      *c = '_';
  if (!profile_build_id(record->start, record->build_id)) {
    memcpy(record->build_id, "unavailable", 12);
    __atomic_store_n(&profile_failed, 1, __ATOMIC_RELAXED);
  }
  dr_free_module_data(module);
  return (int)index;
}

static void profile_dump(void) {
  char path[sizeof(outdir) + 40], line[512];
  dr_snprintf(path, sizeof(path), "%s/../count-locations.tsv", outdir);
  file_t file = dr_open_file(path, DR_FILE_WRITE_REQUIRE_NEW);
  if (file == INVALID_FILE)
    return; /* The harness rejects a missing or incomplete observation. */
  size_t written = 0;
  unsigned emitted = 0;
  dr_fprintf(file, "PROFILE\t3\t%u\t%u\t%u\n", PROFILE_ENTRIES,
             PROFILE_MODULES, PROFILE_FILE_BYTES);
  written = 128; /* Reserve more than the fixed header's maximum length. */
  for (unsigned i = 0; i < PROFILE_SLOTS; ++i) {
    profile_entry_t *entry = &profile_entries[i];
    if (!entry->pc)
      continue;
    int module = profile_module(entry->pc);
    if (module < 0) {
      __atomic_store_n(&profile_failed, 1, __ATOMIC_RELAXED);
      continue;
    }
    int bytes = dr_snprintf(line, sizeof(line), "B\t%d\t%llx\t%u\t%llu\n",
        module, (unsigned long long)((ptr_uint_t)entry->pc - (ptr_uint_t)profile_modules[module].start),
        entry->instructions, (unsigned long long)entry->hits);
    if (bytes <= 0 || (size_t)bytes >= sizeof(line) ||
        written + (size_t)bytes + PROFILE_MODULES * 384U > PROFILE_FILE_BYTES ||
        dr_write_file(file, line, (size_t)bytes) != bytes) {
      __atomic_store_n(&profile_failed, 1, __ATOMIC_RELAXED);
      break;
    }
    written += (size_t)bytes;
    ++emitted;
  }
  for (unsigned i = 0; i < profile_module_count; ++i) {
    profile_module_t *module = &profile_modules[i];
    dr_fprintf(file, "M\t%u\t%s\t%llx\t%llx\t%s\n", i, module->build_id,
               (unsigned long long)(ptr_uint_t)module->start,
               (unsigned long long)(ptr_uint_t)module->end, module->name);
  }
  dr_fprintf(file, "END\t%d\t%u\t%u\t%llu\t%llu\t%llu\t%llu\t%llu\t%llu\n",
      !__atomic_load_n(&profile_failed, __ATOMIC_RELAXED), profile_used, emitted,
      (unsigned long long)profile_charge,
      (unsigned long long)profile_rollback_bytes,
      (unsigned long long)profile_replays,
      (unsigned long long)threads_started, (unsigned long long)threads_ended,
      (unsigned long long)__atomic_load_n(&profile_module_unloads, __ATOMIC_RELAXED));
  dr_close_file(file);
}
