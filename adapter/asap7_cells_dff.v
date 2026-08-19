// asap7 DFF 映射 (techmap) — 对齐 ORFS cells_dff.v 语义:
// 只映射纯 DFF ($_DFF_P_/$_DFF_N_); 使能/复位等复杂类型由 dfflegalize
// 先转成普通 DFF + 逻辑 (RTLIL mux), 再走本文件。VDD/VSS 不在此连接,
// 由 OpenROAD global_connect/PDN 处理。使用前必须先跑 async2sync。

module \$_DFF_P_ (input D, C, output Q);
  DFFHQNx1_ASAP7_75t_R _TECHMAP_REPLACE_ (.D(D), .CLK(C), .Q(Q));
endmodule

module \$_DFF_N_ (input D, C, output Q);
  DFFHQNx1_ASAP7_75t_R _TECHMAP_REPLACE_ (.D(D), .CLK(!C), .Q(Q));
endmodule

// 复杂类型: 声明不支持, 让 dfflegalize 负责转换
module \$_DFFE_PP_ (input D, C, E, output Q);
  wire _TECHMAP_FAIL_ = 1;
endmodule

module \$_DFFE_PN_ (input D, C, E, output Q);
  wire _TECHMAP_FAIL_ = 1;
endmodule

module \$_SDFF_PP0_ (input D, C, R, output Q);
  wire _TECHMAP_FAIL_ = 1;
endmodule

module \$_SDFF_PP1_ (input D, C, R, output Q);
  wire _TECHMAP_FAIL_ = 1;
endmodule

module \$_SDFF_PN0_ (input D, C, R, output Q);
  wire _TECHMAP_FAIL_ = 1;
endmodule

module \$_SDFF_PN1_ (input D, C, R, output Q);
  wire _TECHMAP_FAIL_ = 1;
endmodule

module \$_SDFFE_PP0P_ (input D, C, R, E, output Q);
  wire _TECHMAP_FAIL_ = 1;
endmodule

module \$_SDFFE_PP1P_ (input D, C, R, E, output Q);
  wire _TECHMAP_FAIL_ = 1;
endmodule
