if(UNSAFE_FLAGS)
  set(options -cleancall_ignore_eflags)
endif()
execute_process(COMMAND "${DRRUN}" -quiet -disable_traces ${options} -c "${CLIENT}" -- "${PROGRAM}"
  RESULT_VARIABLE result OUTPUT_VARIABLE output ERROR_VARIABLE trace TIMEOUT 20)
string(STRIP "${trace}" trace)
if(UNSAFE_FLAGS)
  if(NOT "${result}" STREQUAL "1" OR NOT "${output}" STREQUAL "" OR
     NOT "${trace}" STREQUAL "store_load_probe: requires full clean-call flag preservation")
    message(FATAL_ERROR "unsafe option: exit ${result}\nstdout: ${output}\nstderr: ${trace}")
  endif()
elseif(NOT "${result}" STREQUAL "0" OR NOT "${output}" STREQUAL "" OR
   NOT "${trace}" STREQUAL "STORE_LOAD_V1 covered=2 partial=3 disjoint=2")
  message(FATAL_ERROR "store/load fixture exited ${result}\nstdout: ${output}\nstderr: ${trace}")
endif()
