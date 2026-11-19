cmake_minimum_required(VERSION 3.16)
project(durante_store_load C)
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux" OR NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|amd64|AMD64)$")
  message(FATAL_ERROR "The store/load diagnostic requires Linux x86-64")
endif()
find_package(DynamoRIO 11.3 EXACT REQUIRED)
add_library(store_load_probe SHARED probe.c)
configure_DynamoRIO_client(store_load_probe)
use_DynamoRIO_extension(store_load_probe drmgr)
set_target_properties(store_load_probe PROPERTIES C_STANDARD 17 C_STANDARD_REQUIRED YES C_EXTENSIONS YES)
target_compile_options(store_load_probe PRIVATE -Wall -Wextra -Werror)

enable_testing()
add_executable(store_load_range_test range_test.c)
target_compile_options(store_load_range_test PRIVATE -Wall -Wextra -Werror)
add_test(NAME store_load_ranges COMMAND store_load_range_test)

set(DURANTE_TEST_DRRUN "" CACHE FILEPATH "Patched drrun for the instruction-pair fixture")
if(DURANTE_TEST_DRRUN)
  enable_language(ASM)
  add_executable(store_load_fixture fixture.S)
  target_link_options(store_load_fixture PRIVATE -nostdlib -static -no-pie -Wl,--build-id=none)
  add_test(NAME store_load_fixture COMMAND ${CMAKE_COMMAND} -DDRRUN=${DURANTE_TEST_DRRUN}
    -DCLIENT=$<TARGET_FILE:store_load_probe> -DPROGRAM=$<TARGET_FILE:store_load_fixture>
    -P ${CMAKE_CURRENT_SOURCE_DIR}/check_fixture.cmake)
  add_test(NAME store_load_reject_unsafe_flags COMMAND ${CMAKE_COMMAND}
    -DDRRUN=${DURANTE_TEST_DRRUN} -DCLIENT=$<TARGET_FILE:store_load_probe>
    -DPROGRAM=$<TARGET_FILE:store_load_fixture> -DUNSAFE_FLAGS=ON
    -P ${CMAKE_CURRENT_SOURCE_DIR}/check_fixture.cmake)
endif()
