cmake_minimum_required(VERSION 3.16)
project(durante_checkpoint_identity C)
find_package(DynamoRIO 11.3 EXACT REQUIRED)
# The client with every packed write checkpoint also stored as a full address
# and checked against it when settled (DURANTE_TEST_CHECKPOINT_IDENTITY).
add_library(checkpoint_identity SHARED ../../client/durante.c)
configure_DynamoRIO_client(checkpoint_identity)
use_DynamoRIO_extension(checkpoint_identity drmgr)
set_target_properties(checkpoint_identity PROPERTIES C_STANDARD 17 C_STANDARD_REQUIRED YES C_EXTENSIONS YES)
target_compile_options(checkpoint_identity PRIVATE -Wall -Wextra -Werror)
target_compile_definitions(checkpoint_identity PRIVATE DURANTE_TEST_CHECKPOINT_IDENTITY DURANTE_SDK_VERSION="11.3.0")
