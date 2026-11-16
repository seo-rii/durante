cmake_minimum_required(VERSION 3.16)
project(durante_count_locations C ASM)
find_package(DynamoRIO 11.3 EXACT REQUIRED)
add_library(count_locations SHARED ../../client/durante.c)
configure_DynamoRIO_client(count_locations)
use_DynamoRIO_extension(count_locations drmgr)
set_target_properties(count_locations PROPERTIES C_STANDARD 17 C_STANDARD_REQUIRED YES C_EXTENSIONS YES)
target_compile_options(count_locations PRIVATE -Wall -Wextra -Werror)
target_compile_definitions(count_locations PRIVATE DURANTE_TEST_COUNT_LOCATIONS DURANTE_SDK_VERSION="11.3.0")

# Independent static instruction oracle: two setup instructions, seven loop
# iterations of three instructions, then three exit instructions = 26.
add_executable(count_locations_oracle oracle.S)
set_target_properties(count_locations_oracle PROPERTIES LINKER_LANGUAGE C)
target_link_options(count_locations_oracle PRIVATE -nostdlib -static -no-pie -Wl,--build-id=sha1)

# A real unload must reject exit-time module attribution, even if execution and
# ordinary lifecycle accounting both succeed.
add_library(count_locations_unload_module SHARED unload_module.c)
add_executable(count_locations_unload_oracle unload_oracle.c)
target_compile_options(count_locations_unload_oracle PRIVATE -Wall -Wextra -Werror)
target_link_libraries(count_locations_unload_oracle PRIVATE ${CMAKE_DL_LIBS})
