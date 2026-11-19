cmake_minimum_required(VERSION 3.16)
project(durante_l1d_probe C)
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux" OR NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|amd64|AMD64)$")
  message(FATAL_ERROR "The L1D diagnostic requires Linux x86-64")
endif()
find_package(DynamoRIO 11.3 EXACT REQUIRED)
add_library(durante_l1d SHARED ../../client/durante.c)
configure_DynamoRIO_client(durante_l1d)
use_DynamoRIO_extension(durante_l1d drmgr)
set_target_properties(durante_l1d PROPERTIES C_STANDARD 17 C_STANDARD_REQUIRED YES C_EXTENSIONS YES)
target_compile_options(durante_l1d PRIVATE -Wall -Wextra -Werror)
target_compile_definitions(durante_l1d PRIVATE DURANTE_L1D_PROBE DURANTE_SDK_VERSION="11.3.0")

enable_testing()
add_executable(l1d_cache_test cache_test.c)
target_compile_options(l1d_cache_test PRIVATE -Wall -Wextra -Werror)
add_test(NAME l1d_cache_test COMMAND l1d_cache_test)

set(DURANTE_TEST_DRRUN "" CACHE FILEPATH "Patched drrun for the instrumentation control")
if(DURANTE_TEST_DRRUN)
  enable_language(ASM)
  find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
  add_executable(l1d_probe_control control.S)
  target_link_options(l1d_probe_control PRIVATE -nostdlib -static -no-pie -Wl,--build-id=none)
  add_test(NAME l1d_probe_control
    COMMAND ${Python3_EXECUTABLE} -B ${CMAKE_CURRENT_SOURCE_DIR}/check_control.py
      --drrun ${DURANTE_TEST_DRRUN} --client $<TARGET_FILE:durante_l1d>
      --program $<TARGET_FILE:l1d_probe_control>)

  add_executable(l1d_clock_control clock_control.S)
  target_link_options(l1d_clock_control PRIVATE -nostdlib -static -no-pie -Wl,--build-id=none)
  add_test(NAME l1d_clock_control
    COMMAND ${Python3_EXECUTABLE} -B ${CMAKE_CURRENT_SOURCE_DIR}/check_clock.py
      --drrun ${DURANTE_TEST_DRRUN} --client $<TARGET_FILE:durante_l1d>
      --program $<TARGET_FILE:l1d_clock_control>)
endif()
