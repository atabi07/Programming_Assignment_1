`timescale 1ns/1ps
// EC551 Programming Assignment 1 -- SystemVerilog software synthesis engine.
// This testbench is RUN IN A SIMULATOR and is intentionally not synthesizable.
// The file it generates (minimized_logic.v) IS synthesizable structural Verilog.
// Source input: SOP cubes, first token N (2..4); one N-character cube per token.
// Character 1 = positive literal, 0 = negated literal, - = don't-care.
module logic_engine_tb;
    localparam integer MAX_CUBES = 81; // 3^4 possible cubes
    integer n, truth_size;
    reg [15:0] truth;
    string input_path, output_path, wrapper_path;
    integer source_fd, generated_fd, wrapper_fd, scan_rc;
    integer selected_command; // 0: all, 1..12: selected operation
    string cube_text;

    // All valid implicants during a single minimization pass.
    integer cand_mask [0:MAX_CUBES-1];
    integer cand_value[0:MAX_CUBES-1];
    integer cand_count;

    // Pass 0 = minimize ON-set (SOP); pass 1 = minimize OFF-set (POS).
    integer prime_mask  [0:1][0:MAX_CUBES-1];
    integer prime_value [0:1][0:MAX_CUBES-1];
    integer prime_cover [0:1][0:MAX_CUBES-1];
    integer prime_count [0:1];
    integer essential_count[0:1];
    integer chosen_mask [0:1][0:MAX_CUBES-1];
    integer chosen_value[0:1][0:MAX_CUBES-1];
    integer chosen_count[0:1];
    integer literal_count[0:1];

    function automatic integer popcount(input integer number);
        integer i;
        begin
            popcount = 0;
            for (i = 0; i < 16; i = i + 1)
                if ((number & (1 << i)) != 0) popcount = popcount + 1;
        end
    endfunction

    function automatic byte var_letter(input integer pos);
        begin
            case (pos)
                0: var_letter = "A";
                1: var_letter = "B";
                2: var_letter = "C";
                default: var_letter = "D";
            endcase
        end
    endfunction

    function automatic integer covers(input integer m,
                                      input integer mask,
                                      input integer value);
        covers = ((m & mask) == value);
    endfunction

    function automatic integer target_bit(input integer which, input integer m);
        begin
            if (which == 0) target_bit = truth[m];
            else            target_bit = !truth[m];
        end
    endfunction

    // Enumerate ALL cubes; retain valid implicants and discard non-primes.
    // Exact minimum cover: exhaustively compare subsets of prime implicants.
    // Small-variable limit is intentional: exhaustive cover grows exponentially.
    task automatic minimize(input integer which);
        integer mask, value, m, i, j, valid, hit, dominated;
        integer target_bits, owned, owner, cover_count;
        integer subset, best_subset, best_literals, best_terms;
        integer covered_bits, trial_literals, trial_terms, pi_limit;
        begin
            cand_count = 0;
            prime_count[which] = 0;
            essential_count[which] = 0;
            chosen_count[which] = 0;
            literal_count[which] = 0;
            target_bits = 0;
            for (m = 0; m < truth_size; m = m + 1)
                if (target_bit(which, m)) target_bits = target_bits | (1 << m);

            // Enumerate ternary cubes: specified-0 / specified-1 / free.
            for (mask = 0; mask < truth_size; mask = mask + 1) begin
                for (value = 0; value < truth_size; value = value + 1) begin
                    if ((value & mask) == value) begin
                        valid = 1;
                        hit = 0;
                        for (m = 0; m < truth_size; m = m + 1) begin
                            if (covers(m, mask, value)) begin
                                if (!target_bit(which, m)) valid = 0;
                                else hit = 1;
                            end
                        end
                        if (valid && hit) begin
                            cand_mask[cand_count]  = mask;
                            cand_value[cand_count] = value;
                            cand_count = cand_count + 1;
                        end
                    end
                end
            end

            // Prime: there is no valid strictly more general implicant.
            for (i = 0; i < cand_count; i = i + 1) begin
                dominated = 0;
                for (j = 0; j < cand_count; j = j + 1) begin
                    if (cand_mask[i] != cand_mask[j] &&
                        (cand_mask[i] & cand_mask[j]) == cand_mask[j] &&
                        (cand_value[i] & cand_mask[j]) == cand_value[j])
                        dominated = 1;
                end
                if (!dominated) begin
                    prime_mask[which][prime_count[which]]  = cand_mask[i];
                    prime_value[which][prime_count[which]] = cand_value[i];
                    prime_count[which] = prime_count[which] + 1;
                end
            end

            // Compute each prime implicant's minterm coverage.
            for (i = 0; i < prime_count[which]; i = i + 1) begin
                prime_cover[which][i] = 0;
                for (m = 0; m < truth_size; m = m + 1)
                    if (target_bit(which, m) &&
                        covers(m, prime_mask[which][i], prime_value[which][i]))
                        prime_cover[which][i] = prime_cover[which][i] | (1 << m);
            end

            // Unique coverage determines essential prime implicants.
            owned = 0;
            for (m = 0; m < truth_size; m = m + 1) begin
                if (target_bit(which, m)) begin
                    cover_count = 0;
                    owner = 0;
                    for (i = 0; i < prime_count[which]; i = i + 1) begin
                        if ((prime_cover[which][i] & (1 << m)) != 0) begin
                            cover_count = cover_count + 1;
                            owner = i;
                        end
                    end
                    if (cover_count == 1) owned = owned | (1 << owner);
                end
            end
            essential_count[which] = popcount(owned);

            // Search for the globally fewest literals; break ties by term count.
            // Limit is sufficient for the supported 2..4 variables.
            if (prime_count[which] > 18)
                $fatal(1, "Too many primes for this starter implementation");
            pi_limit = (1 << prime_count[which]);
            best_literals = 1000000;
            best_terms = 1000000;
            best_subset = 0;
            for (subset = 0; subset < pi_limit; subset = subset + 1) begin
                // Essential primes must appear in any solution.
                if ((subset & owned) == owned) begin
                    covered_bits = 0;
                    trial_literals = 0;
                    trial_terms = 0;
                    for (i = 0; i < prime_count[which]; i = i + 1) begin
                        if ((subset & (1 << i)) != 0) begin
                            covered_bits = covered_bits | prime_cover[which][i];
                            trial_literals = trial_literals + popcount(prime_mask[which][i]);
                            trial_terms = trial_terms + 1;
                        end
                    end
                    if (covered_bits == target_bits &&
                        (trial_literals < best_literals ||
                        (trial_literals == best_literals && trial_terms < best_terms))) begin
                        best_literals = trial_literals;
                        best_terms = trial_terms;
                        best_subset = subset;
                    end
                end
            end
            if (best_literals == 1000000)
                $fatal(1, "Covering failed -- internal engine error");
            literal_count[which] = best_literals;
            for (i = 0; i < prime_count[which]; i = i + 1) begin
                if ((best_subset & (1 << i)) != 0) begin
                    chosen_mask[which][chosen_count[which]]  = prime_mask[which][i];
                    chosen_value[which][chosen_count[which]] = prime_value[which][i];
                    chosen_count[which] = chosen_count[which] + 1;
                end
            end
        end
    endtask

    // Write a literal (with ' as complement marker) to the simulator console.
    task automatic print_sop_term(input integer mask, input integer value);
        integer j, bitpos;
        begin
            if (mask == 0) $write("1");
            else for (j = 0; j < n; j = j + 1) begin
                bitpos = n - 1 - j;
                if ((mask & (1 << bitpos)) != 0) begin
                    $write("%c", var_letter(j));
                    if ((value & (1 << bitpos)) == 0) $write("'");
                end
            end
        end
    endtask

    task automatic print_pos_term(input integer mask, input integer value);
        integer j, bitpos, first;
        begin
            if (mask == 0) $write("(0)");
            else begin
                first = 1;
                $write("(");
                for (j = 0; j < n; j = j + 1) begin
                    bitpos = n - 1 - j;
                    if ((mask & (1 << bitpos)) != 0) begin
                        if (!first) $write(" + ");
                        $write("%c", var_letter(j));
                        if ((value & (1 << bitpos)) != 0) $write("'");
                        first = 0;
                    end
                end
                $write(")");
            end
        end
    endtask

    // Canonical SOP uses ON-set; canonical POS uses OFF-set.
    // 'inverse' first complements the original function.
    task automatic print_canonical(input integer inverse, input integer pos);
        integer m, j, term_count, bitpos, minterm_is_one, need_term;
        begin
            term_count = 0;
            for (m = 0; m < truth_size; m = m + 1) begin
                minterm_is_one = (truth[m] != inverse);
                need_term = pos ? !minterm_is_one : minterm_is_one;
                if (need_term) begin
                    if (term_count != 0) begin
                        if (pos) $write(" ");
                        else $write(" + ");
                    end
                    if (pos) $write("(");
                    for (j = 0; j < n; j = j + 1) begin
                        bitpos = n - 1 - j;
                        if (pos && j != 0) $write("+");
                        $write("%c", var_letter(j));
                        // SOP minterm: 0 => complemented. POS maxterm: 1 => complemented.
                        if (pos ? ((m & (1 << bitpos)) != 0) :
                                  ((m & (1 << bitpos)) == 0)) $write("'");
                    end
                    if (pos) $write(")");
                    term_count = term_count + 1;
                end
            end
            if (term_count == 0) begin
                if (pos) $write("1");
                else $write("0");
            end
            $write("\n");
        end
    endtask

    task automatic print_minimized(input integer pos);
        integer i;
        begin
            if (chosen_count[pos] == 0) begin
                if (pos) $write("1");
                else $write("0");
            end
            for (i = 0; i < chosen_count[pos]; i = i + 1) begin
                if (i != 0) begin
                    if (pos) $write(" ");
                    else $write(" + ");
                end
                if (pos) print_pos_term(chosen_mask[pos][i], chosen_value[pos][i]);
                else print_sop_term(chosen_mask[pos][i], chosen_value[pos][i]);
            end
            $write("\n");
        end
    endtask

    // Emit actual structural Verilog built from the selected minimal SOP.
    task automatic generate_verilog;
        integer j, i, bitpos, inputs, needs_neg;
        begin
            generated_fd = $fopen(output_path, "w");
            if (generated_fd == 0) $fatal(1, "Cannot open output: %s", output_path);
            $fdisplay(generated_fd, "// AUTO-GENERATED -- structural combinational Verilog");
            $fdisplay(generated_fd, "module minimized_logic (");
            for (j = 0; j < n; j = j + 1)
                $fdisplay(generated_fd, "    input wire %c,", var_letter(j));
            $fdisplay(generated_fd, "    output wire Y");
            $fdisplay(generated_fd, ");");
            if (chosen_count[0] == 0) begin
                $fdisplay(generated_fd, "    assign Y = 1'b0;");
            end else if (chosen_count[0] == 1 && chosen_mask[0][0] == 0) begin
                $fdisplay(generated_fd, "    assign Y = 1'b1;");
            end else begin
                // Only emit inverted signals that are actually used by selected cubes.
                for (j = 0; j < n; j = j + 1) begin
                    bitpos = n - 1 - j;
                    needs_neg = 0;
                    for (i = 0; i < chosen_count[0]; i = i + 1)
                        if ((chosen_mask[0][i] & (1 << bitpos)) != 0 &&
                            (chosen_value[0][i] & (1 << bitpos)) == 0)
                            needs_neg = 1;
                    if (needs_neg) begin
                        $fdisplay(generated_fd, "    wire n%c;", var_letter(j));
                        $fdisplay(generated_fd, "    not (n%c, %c);", var_letter(j), var_letter(j));
                    end
                end
                for (i = 0; i < chosen_count[0]; i = i + 1)
                    $fdisplay(generated_fd, "    wire term%0d;", i);
                for (i = 0; i < chosen_count[0]; i = i + 1) begin
                    inputs = popcount(chosen_mask[0][i]);
                    if (inputs == 1) $fwrite(generated_fd, "    buf (term%0d", i);
                    else             $fwrite(generated_fd, "    and (term%0d", i);
                    for (j = 0; j < n; j = j + 1) begin
                        bitpos = n - 1 - j;
                        if ((chosen_mask[0][i] & (1 << bitpos)) != 0) begin
                            if ((chosen_value[0][i] & (1 << bitpos)) != 0)
                                $fwrite(generated_fd, ", %c", var_letter(j));
                            else
                                $fwrite(generated_fd, ", n%c", var_letter(j));
                        end
                    end
                    $fdisplay(generated_fd, ");");
                end
                if (chosen_count[0] == 1) begin
                    $fdisplay(generated_fd, "    buf (Y, term0);");
                end else begin
                    $fwrite(generated_fd, "    or (Y");
                    for (i = 0; i < chosen_count[0]; i = i + 1)
                        $fwrite(generated_fd, ", term%0d", i);
                    $fdisplay(generated_fd, ");");
                end
            end
            $fdisplay(generated_fd, "endmodule");
            $fclose(generated_fd);

            wrapper_fd = $fopen(wrapper_path, "w");
            if (wrapper_fd == 0) $fatal(1, "Cannot open wrapper: %s", wrapper_path);
            $fdisplay(wrapper_fd, "// AUTO-GENERATED wrapper for Nexys A7, 4 switches / 1 LED");
            $fdisplay(wrapper_fd, "module nexys_top(input wire [3:0] SW, output wire LED0);");
            $fwrite(wrapper_fd, "    minimized_logic circuit (");
            for (j = 0; j < n; j = j + 1)
                $fwrite(wrapper_fd, ".%c(SW[%0d]), ", var_letter(j), j);
            $fdisplay(wrapper_fd, ".Y(LED0));");
            $fdisplay(wrapper_fd, "endmodule");
            $fclose(wrapper_fd);
        end
    endtask

    initial begin : run_engine
        integer m, j, mask, value, symbol, canonical_sop, canonical_pos;
        integer on_count, off_count, sop_y, pos_y, equivalent;
        selected_command = 0;
        scan_rc = $value$plusargs("CMD=%d", selected_command);
        if (selected_command < 0 || selected_command > 12)
            $fatal(1, "CMD must be 0 (all) or 1..12");
        if (!$value$plusargs("INPUT=%s", input_path))
            input_path = "examples/majority.sop";
        if (!$value$plusargs("OUT=%s", output_path))
            output_path = "generated/minimized_logic.v";
        if (!$value$plusargs("TOP=%s", wrapper_path))
            wrapper_path = "generated/nexys_top.v";
        source_fd = $fopen(input_path, "r");
        if (source_fd == 0) $fatal(1, "Cannot open SOP input: %s", input_path);
        scan_rc = $fscanf(source_fd, "%d", n);
        if (scan_rc != 1 || n < 2 || n > 4)
            $fatal(1, "First input token must be variable count 2, 3, or 4");
        truth_size = 1 << n;
        truth = 0;
        while (!$feof(source_fd)) begin
            scan_rc = $fscanf(source_fd, "%s", cube_text);
            if (scan_rc == 1) begin
                if (cube_text.len() != n)
                    $fatal(1, "Each SOP cube must have %0d symbols: %s", n, cube_text);
                mask = 0;
                value = 0;
                for (j = 0; j < n; j = j + 1) begin
                    symbol = cube_text[j];
                    case (symbol)
                        8'h30: mask = mask | (1 << (n - 1 - j)); // 0
                        8'h31: begin // 1
                            mask = mask | (1 << (n - 1 - j));
                            value = value | (1 << (n - 1 - j));
                        end
                        8'h2d: begin end // -
                        default: $fatal(1, "Invalid character in SOP cube: %s", cube_text);
                    endcase
                end
                for (m = 0; m < truth_size; m = m + 1)
                    if (covers(m, mask, value)) truth[m] = 1'b1;
            end
        end
        $fclose(source_fd);

        on_count = 0;
        for (m = 0; m < truth_size; m = m + 1) on_count = on_count + truth[m];
        off_count = truth_size - on_count;
        canonical_sop = on_count * n;
        canonical_pos = off_count * n;
        minimize(0);
        minimize(1);

        $display("===== EC551 LOGIC SYNTHESIS ENGINE =====");
        $display("Inputs: %0d, rows: %0d, command: %0d (0=all)", n, truth_size, selected_command);
        if (selected_command == 0 || selected_command == 1) begin
            $write("1. Canonical SOP       = "); print_canonical(0, 0);
        end
        if (selected_command == 0 || selected_command == 2) begin
            $write("2. Canonical POS       = "); print_canonical(0, 1);
        end
        if (selected_command == 0 || selected_command == 3) begin
            $write("3. Inverse canon. SOP = "); print_canonical(1, 0);
        end
        if (selected_command == 0 || selected_command == 4) begin
            $write("4. Inverse canon. POS = "); print_canonical(1, 1);
        end
        if (selected_command == 0 || selected_command == 5) begin
            $write("5. Minimized SOP       = "); print_minimized(0);
            $display("   Literals saved: %0d (from %0d to %0d)",
                     canonical_sop - literal_count[0], canonical_sop, literal_count[0]);
        end
        if (selected_command == 0 || selected_command == 6) begin
            $write("6. Minimized POS       = "); print_minimized(1);
            $display("   Literals saved: %0d (from %0d to %0d)",
                     canonical_pos - literal_count[1], canonical_pos, literal_count[1]);
        end
        if (selected_command == 0 || selected_command == 7)
            $display("7. Prime implicants: %0d", prime_count[0]);
        if (selected_command == 0 || selected_command == 8)
            $display("8. Essential prime implicants: %0d", essential_count[0]);
        if (selected_command == 0 || selected_command == 9)
            $display("9. ON-set minterms: %0d", on_count);
        if (selected_command == 0 || selected_command == 10)
            $display("10. OFF-set maxterms: %0d", off_count);
        if (selected_command == 0 || selected_command == 11) begin
            $display("11. Custom: truth table (A is most significant):");
            for (m = 0; m < truth_size; m = m + 1) begin
                $write("    ");
                for (j = n - 1; j >= 0; j = j - 1)
                    $write("%0d", (m >> j) & 1);
                $display(" | F=%0d", truth[m]);
            end
        end
        // Always verify internally, even if command 12 was not selected.
        equivalent = 1;
        for (m = 0; m < truth_size; m = m + 1) begin
            sop_y = 0;
            for (j = 0; j < chosen_count[0]; j = j + 1)
                if (covers(m, chosen_mask[0][j], chosen_value[0][j])) sop_y = 1;
            pos_y = 1;
            for (j = 0; j < chosen_count[1]; j = j + 1)
                if (covers(m, chosen_mask[1][j], chosen_value[1][j])) pos_y = 0;
            if (sop_y != truth[m] || pos_y != truth[m]) equivalent = 0;
        end
        if (selected_command == 0 || selected_command == 12)
            $display("12. Custom: SOP/POS equivalence check: %s", equivalent ? "PASS" : "FAIL");
        if (!equivalent) $fatal(1, "The minimized circuit does not match its truth table");
        generate_verilog();
        $display("Generated structural Verilog: %s", output_path);
        $display("Generated FPGA wrapper:      %s", wrapper_path);
        $finish;
    end
endmodule
