cmake_minimum_required(VERSION 3.16)
project(durante_cpuid_profile C)
find_package(DynamoRIO 11.3 EXACT REQUIRED)
add_library(cpuid_profile_client SHARED client.c)
configure_DynamoRIO_client(cpuid_profile_client)
use_DynamoRIO_extension(cpuid_profile_client drmgr)
set_target_properties(cpuid_profile_client PROPERTIES C_STANDARD 17 C_STANDARD_REQUIRED YES C_EXTENSIONS YES)
target_compile_options(cpuid_profile_client PRIVATE -Wall -Wextra -Werror)

# Dynamically linked so glibc's startup CPU dispatch selects string functions.
add_executable(cpuid_profile_probe probe.c)
target_compile_options(cpuid_profile_probe PRIVATE -O2 -Wall -Wextra -Werror -fno-builtin)
