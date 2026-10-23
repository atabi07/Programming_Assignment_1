`timescale 1ns/1ps
// Exhaustively verify automatically generated gate-level Verilog via wrapper.
// This testbench is for Icarus simulation, NOT FPGA synthesis.
module tb_generated;
    reg [3:0] SW;
    wire LED0;
    reg [15:0] expected;
    integer n, m, k, errors, csv_fd;
    string csv_path, wave_path;

    nexys_top dut(.SW(SW), .LED0(LED0));

    initial begin
        if (!$value$plusargs("VARS=%d", n)) n = 3;
        if (!$value$plusargs("EXPECTED=%h", expected)) expected = 16'h00e8;
        if (!$value$plusargs("CSV=%s", csv_path)) csv_path = "simulation.csv";
        if (!$value$plusargs("WAVE=%s", wave_path)) wave_path = "simulation.vcd";
        if (n < 2 || n > 4) $fatal(1, "VARS must be 2, 3, or 4");

        csv_fd = $fopen(csv_path, "w");
        if (csv_fd == 0) $fatal(1, "Cannot write CSV: %s", csv_path);
        $fdisplay(csv_fd, "inputs,actual,expected,result");
        errors = 0;
        SW = 4'b0000;
        $dumpfile(wave_path);
        $dumpvars(0, tb_generated);
        $display("Input  | Actual | Expected | Result");
        $display("-------------------------------------");

        for (m = 0; m < (1 << n); m = m + 1) begin
            // A is MSB in truth-table numbering but is wired to SW[0].
            for (k = 0; k < n; k = k + 1)
                SW[k] = (m >> (n - 1 - k)) & 1;
            #10;
            for (k = 0; k < n; k = k + 1)
                $write("%b", SW[k]);
            for (k = 0; k < n; k = k + 1)
                $fwrite(csv_fd, "%0d", (m >> (n - 1 - k)) & 1);
            if (LED0 === expected[m]) begin
                $display("   |   %b    |    %b     | PASS", LED0, expected[m]);
                $fdisplay(csv_fd, ",%b,%b,PASS", LED0, expected[m]);
            end else begin
                $display("   |   %b    |    %b     | FAIL", LED0, expected[m]);
                $fdisplay(csv_fd, ",%b,%b,FAIL", LED0, expected[m]);
                errors = errors + 1;
            end
        end
        $fclose(csv_fd);
        if (errors != 0) $fatal(1, "FAILED: %0d incorrect truth-table rows", errors);
        $display("PASS: all %0d generated-netlist combinations", (1 << n));
        $finish;
    end
endmodule
