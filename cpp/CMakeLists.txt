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

add_library(cbcore core/reader.cpp core/model.cpp core/parse.cpp core/geometry.cpp core/writer.cpp core/estimate.cpp core/summary.cpp core/plan.cpp core/tower.cpp)
target_include_directories(cbcore PUBLIC core)

add_executable(dump_events tools/dump_events.cpp)
target_link_libraries(dump_events cbcore)

add_executable(dump_program tools/dump_program.cpp)
target_link_libraries(dump_program cbcore)

add_executable(write_bands tools/write_bands.cpp)
target_link_libraries(write_bands cbcore)

add_executable(finish tools/finish.cpp)
target_link_libraries(finish cbcore)

add_executable(plan_bands tools/plan_bands.cpp)
target_link_libraries(plan_bands cbcore)
