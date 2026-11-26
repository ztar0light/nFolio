# Put the Ndless SDK bin directory and ARM GNU toolchain on PATH.
BASE = src/appearance.c src/shelf.c src/book.c src/codec.c src/save.c third_party/lzma/LzmaDec.c third_party/miniz/miniz_tinfl.c
NATIVE = $(BASE) src/app.c src/ndless.c
CPPFLAGS = -Isrc -Ithird_party -Ithird_party/miniz -Ithird_party/lzma -DMINIZ_NO_STDIO -DMINIZ_NO_TIME -DMINIZ_NO_ARCHIVE_APIS -DMINIZ_NO_ZLIB_APIS -DMINIZ_NO_DEFLATE_APIS
CFLAGS = -std=gnu11 -O2 -Wall -Wextra -ffunction-sections -fdata-sections -fno-strict-aliasing
ARM_OBJS = $(NATIVE:%.c=build/arm/%.o)
.PHONY: all clean host
all: Folio.tns
build/arm/%.o: %.c
	@mkdir -p $(dir $@)
	nspire-gcc $(CPPFLAGS) $(CFLAGS) -marm -mcpu=arm926ej-s -MMD -MP -c $< -o $@
build/Folio.elf: $(ARM_OBJS)
	nspire-ld $^ -Wl,--gc-sections -o $@
Folio.tns: build/Folio.elf
	genzehn --input $< --output $@ --name Folio --version 8 --ndless-min 42 --ndless-rev-min 2004 --clickpad-support 0 --touchpad-support 1 --color-support 1 --32MB-support 1 --240x320-support 1 --uses-lcd-blit 1 --compress
host: build/folio-host
build/folio-host: $(BASE) src/app.c tests/host.c src/folio.h src/codec.h src/device.h src/shelf.h src/appearance.h src/ui_font.h
	@mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(BASE) src/app.c tests/host.c -lm -o $@
clean:
	rm -rf build Folio.tns
-include $(ARM_OBJS:.o=.d)
