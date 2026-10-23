# EC551 PA1 optional Vivado batch build (no FPGA programming).
# Usage from project root:
#   vivado -mode batch -source scripts/build_bitstream.tcl -tclargs output/majority_sop
if {$argc != 1} {
    error "Usage: vivado -mode batch -source scripts/build_bitstream.tcl -tclargs output/<case_name>"
}
set root [file normalize [file join [file dirname [info script]] ..]]
set case_dir [file normalize [lindex $argv 0]]
set logic_v [file join $case_dir verilog minimized_logic.v]
set wrapper_v [file join $case_dir verilog nexys_top.v]
set xdc [file join $root constraints nexys_a7_100t.xdc]
foreach f [list $logic_v $wrapper_v $xdc] {
    if {![file isfile $f]} {error "File not found: $f"}
}
set project_dir [file join $case_dir vivado_build]
create_project ec551_demo $project_dir -part xc7a100tcsg324-1 -force
add_files [list $logic_v $wrapper_v]
add_files -fileset constrs_1 $xdc
set_property top nexys_top [current_fileset]
update_compile_order -fileset sources_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1
set synth_status [get_property STATUS [get_runs synth_1]]
puts "Synthesis status: $synth_status"
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis was unsuccessful"}
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
set impl_status [get_property STATUS [get_runs impl_1]]
puts "Implementation status: $impl_status"
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation or bitstream generation was unsuccessful"}
set bit [file join $project_dir ec551_demo.runs impl_1 nexys_top.bit]
if {![file isfile $bit]} {error "Bitstream expected but not found: $bit"}
puts "SUCCESS: bitstream saved to $bit"
puts "NOT PROGRAMMED: Open Vivado Hardware Manager and program the connected Nexys A7 with that .bit file."
