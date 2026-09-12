add_rules("mode.debug", "mode.release", "mode.releasedbg")

add_requires("boost 1.83.0", {configs = {program_options = true}})
add_requires("zstd 1.5.7", "fmt 9.1.0", "abseil 20260526.0", "onnxruntime 1.22.0")
add_packages("boost", "zstd", "fmt", "abseil", "onnxruntime")

set_languages("c++20")
set_rundir('$(projectdir)')
add_cflags("-fno-omit-frame-pointer", "-ggdb3")
add_cxxflags("-fno-omit-frame-pointer", "-ggdb3")

-- add asan when in debug mode
-- if is_mode("debug") then
--     add_cflags("-fsanitize=address")
--     add_cxxflags("-fsanitize=address")
--     add_ldflags("-fsanitize=address")
--     -- fix libboost python dependency
--     add_ldflags("-L/usr/lib/python3.12/config-3.12-x86_64-linux-gnu -L/usr/lib/x86_64-linux-gnu -lpython3.12 -ldl  -lm")
-- end

if is_mode('releasedbg') then
    set_strip('none')
end

target('sim')
    set_kind('binary')
    add_files('sim/sim.cpp')
    add_files('sim/eviction.cpp')

target('oracle_backpass')
    set_kind('binary')
    add_files('sim/oracle_backpass.cpp')
