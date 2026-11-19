cmake_minimum_required(VERSION 3.16)
project(durante_time_features C)
find_package(DynamoRIO 11.3 EXACT REQUIRED)
# Exploratory feature client; not the production meter.
add_library(time_features_client SHARED featclient.c)
configure_DynamoRIO_client(time_features_client)
use_DynamoRIO_extension(time_features_client drmgr)
use_DynamoRIO_extension(time_features_client drcontainers)
set_target_properties(time_features_client PROPERTIES C_STANDARD 17 C_STANDARD_REQUIRED YES C_EXTENSIONS YES)
target_compile_options(time_features_client PRIVATE -Wall -Wextra -Werror)
# The corpus uses the judge's ordinary C flags; hot loops make no library calls.
add_executable(time_features_work work.c)
target_compile_options(time_features_work PRIVATE -O2 -Wall -Wextra -Werror)
target_link_options(time_features_work PRIVATE -static)

set(DURANTE_TEST_DRRUN "" CACHE FILEPATH "Patched drrun for the loop recurrence diagnostic fixtures")
if(DURANTE_TEST_DRRUN)
  enable_testing()
  enable_language(ASM)
  find_package(Python3 REQUIRED COMPONENTS Interpreter)
  foreach(iterations IN ITEMS 1 9)
    add_executable(recurrence_${iterations} ../../tests/latency_bound/kernels.S)
    target_compile_definitions(recurrence_${iterations} PRIVATE MODE=1 ITERATIONS=${iterations})
  endforeach()
  add_executable(recurrence_threads ../../tests/clock_threads.S)
  foreach(fixture IN ITEMS recurrence_1 recurrence_9 recurrence_threads)
    target_link_options(${fixture} PRIVATE -nostdlib -static -no-pie -Wl,--build-id=none)
  endforeach()
  add_test(NAME time_features_loop_recurrence COMMAND Python3::Interpreter
    ${CMAKE_CURRENT_SOURCE_DIR}/check_recurrence.py ${DURANTE_TEST_DRRUN}
    $<TARGET_FILE:time_features_client> $<TARGET_FILE:recurrence_1>
    $<TARGET_FILE:recurrence_9> $<TARGET_FILE:recurrence_threads>)
endif()
