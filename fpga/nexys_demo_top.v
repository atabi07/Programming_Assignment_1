// Hand-written demo wrapper for the Nexys A7-100T (allowed by the assignment).
// Connects the GENERATED structural module minimized_logic to more board I/O:
//   SW0..SW3      -> inputs A..D           (A = SW0, same as generated wrapper)
//   LED0          -> output Y              (unchanged from generated wrapper)
//   RGB LED16     -> green when Y = 1, red when Y = 0
//   7-seg right   -> current truth-table row number in hex (A = MSB, matches command 11)
//   7-seg left    -> value of Y (1 or 0)
// minimized_logic itself is NOT modified; this file only wires it to pins.
module nexys_demo_top #(
    parameter N = 4                      // number of inputs: 2, 3, or 4
) (
    input  wire       CLK100MHZ,
    input  wire [3:0] SW,
    output wire       LED0,
    output wire       LED16_R,
    output wire       LED16_G,
    output wire       LED16_B,
    output reg  [6:0] SEG,               // {CG,CF,CE,CD,CC,CB,CA}, active low
    output wire       DP,                // decimal point, active low
    output reg  [7:0] AN                 // digit enables, active low
);
    wire y;

    // EDIT THIS LINE to match the generated module's ports:
    //   N = 2: .A(SW[0]), .B(SW[1]),                         .Y(y)
    //   N = 3: .A(SW[0]), .B(SW[1]), .C(SW[2]),              .Y(y)
    //   N = 4: .A(SW[0]), .B(SW[1]), .C(SW[2]), .D(SW[3]),   .Y(y)
    minimized_logic circuit (.A(SW[0]), .B(SW[1]), .C(SW[2]), .D(SW[3]), .Y(y));
    // Row number in truth-table order: A is the most significant bit.
    wire [3:0] row = (N == 4) ? {SW[0], SW[1], SW[2], SW[3]} :
                     (N == 3) ? {1'b0,  SW[0], SW[1], SW[2]} :
                                {2'b00, SW[0], SW[1]};

    // Free-running counter: 7-seg digit multiplexing and RGB dimming.
    reg [16:0] refresh = 17'd0;
    always @(posedge CLK100MHZ) refresh <= refresh + 17'd1;

    assign LED0 = y;

    // RGB LEDs are very bright at full duty, so run them at 1/8 duty.
    wire dim = &refresh[2:0];
    assign LED16_G = y  & dim;
    assign LED16_R = ~y & dim;
    assign LED16_B = 1'b0;

    assign DP = 1'b1;                    // decimal point off

    // Alternate between two digits about 760 times per second.
    reg [3:0] digit;
    always @* begin
        if (refresh[16]) begin
            AN    = 8'b0111_1111;        // leftmost digit: Y
            digit = {3'b000, y};
        end else begin
            AN    = 8'b1111_1110;        // rightmost digit: row number
            digit = row;
        end
    end

    // Hex digit -> segments, {g,f,e,d,c,b,a}, active low.
    always @* begin
        case (digit)
            4'h0: SEG = 7'b1000000;
            4'h1: SEG = 7'b1111001;
            4'h2: SEG = 7'b0100100;
            4'h3: SEG = 7'b0110000;
            4'h4: SEG = 7'b0011001;
            4'h5: SEG = 7'b0010010;
            4'h6: SEG = 7'b0000010;
            4'h7: SEG = 7'b1111000;
            4'h8: SEG = 7'b0000000;
            4'h9: SEG = 7'b0010000;
            4'hA: SEG = 7'b0001000;
            4'hB: SEG = 7'b0000011;
            4'hC: SEG = 7'b1000110;
            4'hD: SEG = 7'b0100001;
            4'hE: SEG = 7'b0000110;
            default: SEG = 7'b0001110;   // F
        endcase
    end
endmodule
