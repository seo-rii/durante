cmake_minimum_required(VERSION 3.16)
project(durante C)

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux" OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
  message(FATAL_ERROR "This diagnostic client currently supports Linux x86-64 only")
endif()
find_package(DynamoRIO 11.3 EXACT REQUIRED)
add_library(durante SHARED durante.c)
configure_DynamoRIO_client(durante)
use_DynamoRIO_extension(durante drmgr)
# SDK 11.3 defines bool itself; GCC 15 defaults to C23, where it is a keyword.
set_target_properties(durante PROPERTIES C_STANDARD 17 C_STANDARD_REQUIRED YES C_EXTENSIONS YES)
target_compile_options(durante PRIVATE -Wall -Wextra -Werror)
target_compile_definitions(durante PRIVATE DURANTE_SDK_VERSION="11.3.0")
