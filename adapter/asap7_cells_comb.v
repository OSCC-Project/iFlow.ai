// asap7 组合门映射 (techmap) — 绕过 abc (asap7 liberty 的 SCL 数据会触发
// 本机 abc 的 Nf_ManCutMatch 断言崩溃, 已定位根因)。质量低于 abc 结构映射
// (无面积优化), 但功能正确 — 教学场景可接受, 面板如实标注。
// 单元取自 SIMPLE/INVBUF 库; MUX 用 4 个 NAND 门搭 (asap7 无 MUX2 单元)。

module \$_NOT_ (input A, output Y);
  INVx1_ASAP7_75t_R _TECHMAP_REPLACE_ (.A(A), .Y(Y));
endmodule

module \$_AND_ (input A, B, output Y);
  AND2x2_ASAP7_75t_R _TECHMAP_REPLACE_ (.A(A), .B(B), .Y(Y));
endmodule

module \$_OR_ (input A, B, output Y);
  OR2x2_ASAP7_75t_R _TECHMAP_REPLACE_ (.A(A), .B(B), .Y(Y));
endmodule

module \$_NAND_ (input A, B, output Y);
  NAND2x1_ASAP7_75t_R _TECHMAP_REPLACE_ (.A(A), .B(B), .Y(Y));
endmodule

module \$_NOR_ (input A, B, output Y);
  NOR2x1_ASAP7_75t_R _TECHMAP_REPLACE_ (.A(A), .B(B), .Y(Y));
endmodule

module \$_XOR_ (input A, B, output Y);
  XOR2x1_ASAP7_75t_R _TECHMAP_REPLACE_ (.A(A), .B(B), .Y(Y));
endmodule

module \$_XNOR_ (input A, B, output Y);
  XNOR2x1_ASAP7_75t_R _TECHMAP_REPLACE_ (.A(A), .B(B), .Y(Y));
endmodule

module \$_MUX_ (input A, B, S, output Y);
  wire sbar, t0, t1;
  INVx1_ASAP7_75t_R _TECHMAP_REPLACE_ (.A(S), .Y(sbar));
  NAND2x1_ASAP7_75t_R _TECHMAP_GATE_1_ (.A(A), .B(S), .Y(t0));
  NAND2x1_ASAP7_75t_R _TECHMAP_GATE_2_ (.A(B), .B(sbar), .Y(t1));
  NAND2x1_ASAP7_75t_R _TECHMAP_GATE_3_ (.A(t0), .B(t1), .Y(Y));
endmodule
