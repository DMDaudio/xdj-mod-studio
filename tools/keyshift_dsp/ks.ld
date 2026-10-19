ENTRY(ks_pull)
SECTIONS {
  . = 0xC005F4C0;
  .text : { *(.text.ks_pull) *(.text*) }
  .rodata : { *(.rodata*) *(.const*) }
  . = ALIGN(8);
  KS = .;
  . += 0x260;
  /DISCARD/ : { *(.data*) *(.bss*) *(.neardata*) *(.fardata*) *(.comment) *(.c6xabi.*) }
}
