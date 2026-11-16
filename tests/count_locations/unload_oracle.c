#include <dlfcn.h>

int main(int argc, char **argv) {
  if (argc != 2)
    return 1;
  void *module = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  if (!module)
    return 2;
  int (*value)(void) = (int (*)(void))dlsym(module, "count_locations_unload_value");
  int result = value && value() == 17 ? 0 : 3;
  if (dlclose(module) != 0)
    return 4;
  return result;
}
