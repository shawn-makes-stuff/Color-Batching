cmake_minimum_required(VERSION 3.20)
project(ColorBatching CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(MSVC)
  # exact IEEE arithmetic (no contraction into FMA, no reassociation): results must match the Python reference
  add_compile_options(/fp:precise /W4 /utf-8)
  add_compile_definitions(_CRT_SECURE_NO_WARNINGS)
else()
  add_compile_options(-ffp-contract=off -Wall -Wextra)
endif()
find_package(Threads REQUIRED)

add_library(cbcore core/reader.cpp core/model.cpp core/parse.cpp core/geometry.cpp core/writer.cpp core/estimate.cpp
            core/summary.cpp core/plan.cpp core/tower.cpp core/staggered.cpp core/json.cpp core/config.cpp
            core/pipeline.cpp)
target_include_directories(cbcore PUBLIC core)
target_link_libraries(cbcore PUBLIC Threads::Threads)

# the tool and the app
if(WIN32)
  enable_language(RC)
  set(ICON app/icon.rc)      # the exe icon
endif()
add_executable(color-batching cli/main.cpp ${ICON})
target_link_libraries(color-batching cbcore)

add_executable(color-batching-app app/main.cpp ${ICON})
target_link_libraries(color-batching-app cbcore)
if(WIN32)
  target_link_libraries(color-batching-app ws2_32 shell32)
endif()

# comparison tools (tools/dump_*.py write the Python side)
foreach(t dump_events dump_program write_bands finish plan_bands)
  add_executable(${t} tools/${t}.cpp)
  target_link_libraries(${t} cbcore)
endforeach()
