##
## This file is part of the libsigrokdecode project.
##
## Copyright (C) 2012-2020 Uwe Hermann <uwe@hermann-uwe.de>
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, see <http://www.gnu.org/licenses/>.
##

import sigrokdecode as srd
from common.srdhelper import SrdIntEnum
from common.sdcard import (cmd_names, acmd_names)

responses = '1 1b 2 3 7'.split()

a = ['CMD%d' % i for i in range(64)] + ['ACMD%d' % i for i in range(64)] + \
    ['R' + r.upper() for r in responses] + ['BIT', 'BIT_WARNING']
Ann = SrdIntEnum.from_list('Ann', a)

class Decoder(srd.Decoder):
    api_version = 3
    id = 'sdcard_spi'
    name = 'SD card (SPI mode)'
    longname = 'Secure Digital card (SPI mode)'
    desc = 'Secure Digital card (SPI mode) low-level protocol.'
    license = 'gplv2+'
    inputs = ['spi']
    outputs = []
    tags = ['Memory']
    annotations = \
        tuple(('cmd%d' % i, 'CMD%d' % i) for i in range(64)) + \
        tuple(('acmd%d' % i, 'ACMD%d' % i) for i in range(64)) + \
        tuple(('r%s' % r, 'R%s response' % r) for r in responses) + ( \
        ('bit', 'Bit'),
        ('bit-warning', 'Bit warning'),
    )
    annotation_rows = (
        ('bits', 'Bits', (Ann.BIT, Ann.BIT_WARNING)),
        ('commands-replies', 'Commands/replies', Ann.prefixes('CMD ACMD R')),
    )

    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 'IDLE'
        self.ss, self.es = 0, 0
        self.ss_bit, self.es_bit = 0, 0
        self.ss_cmd, self.es_cmd = 0, 0
        self.ss_busy, self.es_busy = 0, 0
        self.cmd_token = []
        self.cmd_token_bits = []
        self.is_acmd = False # Indicates CMD vs. ACMD
        self.blocklen = 0
        self.read_buf = []
        self.read_bits = []
        self.cmd_str = ''
        self.is_cmd24 = False
        self.cmd24_start_token_found = False
        self.is_cmd17 = False
        self.cmd17_start_token_found = False
        self.busy_first_byte = False

    def start(self):
        self.out_ann = self.register(srd.OUTPUT_ANN)

    def putx(self, data):
        self.put(self.ss_cmd, self.es_cmd, self.out_ann, data)

    def putc(self, cmd, desc):
        self.putx([cmd, ['%s: %s' % (self.cmd_str, desc)]])

    def putb(self, data):
        self.put(self.ss_bit, self.es_bit, self.out_ann, data)

    def cmd_name(self, cmd):
        c = acmd_names if self.is_acmd else cmd_names
        s = c.get(cmd, 'Unknown')
        # SD mode names for CMD32/33: ERASE_WR_BLK_{START,END}.
        # SPI mode names for CMD32/33: ERASE_WR_BLK_{START,END}_ADDR.
        if cmd in (32, 33):
            s += '_ADDR'
        return s

    def handle_command_token(self, mosi, miso):
        # Command tokens (6 bytes) are sent (MSB-first) by the host.
        #
        # Format:
        #  - CMD[47:47]: Start bit (always 0)
        #  - CMD[46:46]: Transmitter bit (1 == host)
        #  - CMD[45:40]: Command index (BCD; valid: 0-63)
        #  - CMD[39:08]: Argument
        #  - CMD[07:01]: CRC7
        #  - CMD[00:00]: End bit (always 1)

        if len(self.cmd_token) == 0:
            self.ss_cmd = self.ss

        self.cmd_token.append(mosi)
        self.cmd_token_bits.append(self.mosi_bits)

        # All command tokens are 6 bytes long.
        if len(self.cmd_token) < 6:
            return

        self.es_cmd = self.es

        t = self.cmd_token

        def tb(byte, bit):
            return self.cmd_token_bits[5 - byte][bit]

        # Bits[47:47]: Start bit (always 0)
        bit, self.ss_bit, self.es_bit = tb(5, 7)[0], tb(5, 7)[1], tb(5, 7)[2]
        if bit == 0:
            self.putb([Ann.BIT, ['Start bit: %d' % bit]])
        else:
            self.putb([Ann.BIT_WARNING, ['Start bit: %s (Warning: Must be 0!)' % bit]])

        # Bits[46:46]: Transmitter bit (1 == host)
        bit, self.ss_bit, self.es_bit = tb(5, 6)[0], tb(5, 6)[1], tb(5, 6)[2]
        if bit == 1:
            self.putb([Ann.BIT, ['Transmitter bit: %d' % bit]])
        else:
            self.putb([Ann.BIT_WARNING, ['Transmitter bit: %d (Warning: Must be 1!)' % bit]])

        # Bits[45:40]: Command index (BCD; valid: 0-63)
        cmd = self.cmd_index = t[0] & 0x3f
        self.ss_bit, self.es_bit = tb(5, 5)[1], tb(5, 0)[2]
        # Leave ACMD mode if no intervening ACMD since previous CMD55.
        if cmd == 55:
            self.is_acmd = False
        elif cmd not in (13, 18, 22, 23, 25, 26, 38, 41, 42, 43, 44, 45, 46, 47, 48, 49, 51):
            self.is_acmd = False
        # CMD or ACMD?
        s = 'ACMD' if self.is_acmd else 'CMD'
        self.putb([Ann.BIT, ['Command: %s%d (%s)' % (s, cmd, self.cmd_name(cmd))]])

        # Bits[39:8]: Argument
        self.arg = (t[1] << 24) | (t[2] << 16) | (t[3] << 8) | t[4]
        self.ss_bit, self.es_bit = tb(4, 7)[1], tb(1, 0)[2]
        self.putb([Ann.BIT, ['Argument: 0x%04x' % self.arg]])

        # Bits[7:1]: CRC7
        # TODO: Check CRC7.
        crc = t[5] >> 1
        self.ss_bit, self.es_bit = tb(0, 7)[1], tb(0, 1)[2]
        self.putb([Ann.BIT, ['CRC7: 0x%01x' % crc]])

        # Bits[0:0]: End bit (always 1)
        bit, self.ss_bit, self.es_bit = tb(0, 0)[0], tb(0, 0)[1], tb(0, 0)[2]
        if bit == 1:
            self.putb([Ann.BIT, ['End bit: %d' % bit]])
        else:
            self.putb([Ann.BIT_WARNING, ['End bit: %d (Warning: Must be 1!)' % bit]])

        # Handle command.
        if cmd in (0, 1, 8, 9, 13, 16, 17, 24, 41, 49, 55, 58, 59):
            self.state = 'HANDLE CMD%d' % cmd
            self.cmd_str = '%s%d (%s)' % (s, cmd, self.cmd_name(cmd))
        else:
            self.state = 'HANDLE CMD999'
            a = '%s%d: %02x %02x %02x %02x %02x %02x' % ((s, cmd) + tuple(t))
            self.putx([cmd, [a]])

    def handle_cmd0(self):
        # CMD0: GO_IDLE_STATE
        self.putc(Ann.CMD0, 'Reset the SD card')
        self.state = 'GET RESPONSE R1'

    def handle_cmd1(self):
        # CMD1: SEND_OP_COND
        self.putc(Ann.CMD1, 'Send HCS info and activate the card init process')
        hcs = (self.arg & (1 << 30)) >> 30
        self.ss_bit = self.cmd_token_bits[5 - 4][6][1]
        self.es_bit = self.cmd_token_bits[5 - 4][6][2]
        self.putb([Ann.BIT, ['HCS: %d' % hcs]])
        self.state = 'GET RESPONSE R1'

    def handle_cmd8(self):
        # CMD8: SEND_IF_COND
        self.putc(Ann.CMD8, 'Send interface condition')
        self.read_buf = []
        self.read_bits = []
        self.state = 'GET RESPONSE R7'

    def handle_cmd9(self):
        # CMD9: SEND_CSD (128 bits / 16 bytes)
        self.putc(Ann.CMD9, 'Ask card to send its card specific data (CSD)')
        if len(self.read_buf) == 0:
            self.ss_cmd = self.ss
        self.read_buf.append(self.miso)
        # FIXME
        ### if len(self.read_buf) < 16:
        if len(self.read_buf) < 16 + 4:
            return
        self.es_cmd = self.es
        self.read_buf = self.read_buf[4:] # TODO: Document or redo.
        self.putx([Ann.CMD9, ['CSD: %s' % self.read_buf]])
        # TODO: Decode all bits.
        self.read_buf = []
        ### self.state = 'GET RESPONSE R1'
        self.state = 'IDLE'

    def handle_cmd10(self):
        # CMD10: SEND_CID (128 bits / 16 bytes)
        self.putc(Ann.CMD10, 'Ask card to send its card identification (CID)')
        self.read_buf.append(self.miso)
        if len(self.read_buf) < 16:
            return
        self.putx([Ann.CMD10, ['CID: %s' % self.read_buf]])
        # TODO: Decode all bits.
        self.read_buf = []
        self.state = 'GET RESPONSE R1'

    def handle_cmd13(self):
        # CMD13: SEND_STATUS
        self.putc(Ann.CMD13, 'Ask card to send its status register')
        self.read_buf = []
        self.state = 'GET RESPONSE R2'

    def handle_cmd16(self):
        # CMD16: SET_BLOCKLEN
        self.blocklen = self.arg
        # TODO: Sanity check on block length.
        self.putc(Ann.CMD16, 'Set the block length to %d bytes' % self.blocklen)
        self.state = 'GET RESPONSE R1'

    def handle_cmd17(self):
        # CMD17: READ_SINGLE_BLOCK
        self.putc(Ann.CMD17, 'Read a block from address 0x%04x' % self.arg)
        self.is_cmd17 = True
        self.state = 'GET RESPONSE R1'

    def handle_cmd24(self):
        # CMD24: WRITE_BLOCK
        self.putc(Ann.CMD24, 'Write a block to address 0x%04x' % self.arg)
        self.is_cmd24 = True
        self.state = 'GET RESPONSE R1'

    def handle_cmd49(self):
        self.state = 'GET RESPONSE R1'

    def handle_cmd55(self):
        # CMD55: APP_CMD
        self.putc(Ann.CMD55, 'Next command is an application-specific command')
        self.is_acmd = True
        self.state = 'GET RESPONSE R1'

    def handle_cmd58(self):
        # CMD58: READ_OCR
        self.putc(Ann.CMD58, 'Read the OCR register')
        self.read_buf = []
        self.read_bits = []
        self.state = 'GET RESPONSE R3'

    def handle_cmd59(self):
        # CMD59: CRC_ON_OFF
        crc_on_off = self.arg & (1 << 0)
        s = 'on' if crc_on_off == 1 else 'off'
        self.putc(Ann.CMD59, 'Turn the SD card CRC option %s' % s)
        self.state = 'GET RESPONSE R1'

    def handle_acmd41(self):
        # ACMD41: SD_SEND_OP_COND
        self.putc(Ann.ACMD41, 'Send HCS info and activate the card init process')
        self.state = 'GET RESPONSE R1'

    def handle_cmd999(self):
        self.state = 'GET RESPONSE R1'

    def handle_cid_register(self):
        # Card Identification (CID) register, 128bits

        cid = self.cid

        # Manufacturer ID: CID[127:120] (8 bits)
        mid = cid[15]

        # OEM/Application ID: CID[119:104] (16 bits)
        oid = (cid[14] << 8) | cid[13]

        # Product name: CID[103:64] (40 bits)
        pnm = 0
        for i in range(12, 8 - 1, -1):
            pnm <<= 8
            pnm |= cid[i]

        # Product revision: CID[63:56] (8 bits)
        prv = cid[7]

        # Product serial number: CID[55:24] (32 bits)
        psn = 0
        for i in range(6, 3 - 1, -1):
            psn <<= 8
            psn |= cid[i]

        # RESERVED: CID[23:20] (4 bits)

        # Manufacturing date: CID[19:8] (12 bits)
        # TODO

        # CRC7 checksum: CID[7:1] (7 bits)
        # TODO

        # Not used, always 1: CID[0:0] (1 bit)
        # TODO

    def handle_response_r1(self, res):
        # The R1 response token format (1 byte).
        # Sent by the card after every command except for SEND_STATUS.

        self.ss_cmd, self.es_cmd = self.miso_bits[7][1], self.miso_bits[0][2]
        self.putx([Ann.R1, ['R1: 0x%02x' % res]])

        def putbit(bit, data):
            b = self.miso_bits[bit]
            self.ss_bit, self.es_bit = b[1], b[2]
            self.putb([Ann.BIT, data])

        # Bit 0: 'In idle state' bit
        s = '' if (res & (1 << 0)) else 'not '
        putbit(0, ['Card is %sin idle state' % s])

        # Bit 1: 'Erase reset' bit
        s = '' if (res & (1 << 1)) else 'not '
        putbit(1, ['Erase sequence %scleared' % s])

        # Bit 2: 'Illegal command' bit
        s = 'I' if (res & (1 << 2)) else 'No i'
        putbit(2, ['%sllegal command detected' % s])

        # Bit 3: 'Communication CRC error' bit
        s = 'failed' if (res & (1 << 3)) else 'was successful'
        putbit(3, ['CRC check of last command %s' % s])

        # Bit 4: 'Erase sequence error' bit
        s = 'E' if (res & (1 << 4)) else 'No e'
        putbit(4, ['%srror in the sequence of erase commands' % s])

        # Bit 5: 'Address error' bit
        s = 'M' if (res & (1 << 5)) else 'No m'
        putbit(5, ['%sisaligned address used in command' % s])

        # Bit 6: 'Parameter error' bit
        s = '' if (res & (1 << 6)) else 'not '
        putbit(6, ['Command argument %soutside allowed range' % s])

        # Bit 7: Always set to 0
        putbit(7, ['Bit 7 (always 0)'])

        if self.is_cmd17:
            self.state = 'HANDLE DATA BLOCK CMD17'
        if self.is_cmd24:
            self.state = 'HANDLE DATA BLOCK CMD24'

    def handle_response_r1b(self, res):
        # TODO
        pass

    def handle_response_r2(self, res):
        # The R2 response token format (2 bytes).
        # Sent as a response to the SEND_STATUS command.
        # The structure of the first (MSB) byte is identical to response type R1.
        # The second byte contains the contents of the card status register.

        def putbit(bit, data):
            b = self.miso_bits[bit]
            self.ss_bit, self.es_bit = b[1], b[2]
            self.putb([Ann.BIT, data])

        self.es_cmd = self.es
        self.read_buf.append(res)
        if len(self.read_buf) == 1:
            self.handle_response_r1(res)
            self.putx([Ann.R2, ['R2: 0x%02x' % res]])
        else:
            r1 = self.read_buf[0]
            status = self.read_buf[1]
            self.putx([Ann.R2, ['R2: [R1: 0x%02x, Status: 0x%02x]' % (r1, status)]])

            # Bit 0: 'Card is locked' bit
            s = '' if (status & (1 << 0)) else 'un'
            putbit(0, ['Card is %slocked' % s])

            # Bit 1: 'Write protect erase skip | lock/unlock command failed' bit
            w = 'A' if (status & (1 << 1)) else 'No a'
            l = 'E' if (status & (1 << 1)) else 'No e'
            putbit(1, ['%sttempt to erase a write-protected sector | %srror in card lock/unlock operation' % (w, l)])

            # Bit 2: 'Error:' bit
            s = 'G' if (status & (1 << 2)) else 'No g'
            putbit(2, ['%seneral or unknown error' % s])

            # Bit 3: 'CC error' bit
            s = 'I' if (status & (1 << 3)) else 'No i'
            putbit(3, ['%snternal card controller error' % s])

            # Bit 4: 'Card ECC failed' bit
            s = '' if (status & (1 << 4)) else 'No '
            putbit(4, ['%sECC failure to correct data' % s])

            # Bit 5: 'Write protect violation' bit
            s = 'W' if (res & (1 << 5)) else 'No w'
            putbit(5, ['%srite protect violation' % s])

            # Bit 6: 'Erase param' bit
            s = 'I' if (res & (1 << 6)) else 'No i'
            putbit(6, ['%snvalid selection for erase, sectors or groups' % s])

            # Bit 7: 'Out of range | csd overwrite' bit
            r = 'O' if (status & (1 << 7)) else 'No o'
            c = 'C' if (status & (1 << 7)) else 'No C'
            putbit(7, ['%sut of range | %sSD overwrite' % (r, c)])

            self.state = 'IDLE'
            self.read_buf = []

    def handle_response_r3(self, res):
        # The R3 response token format (5 bytes).
        # Sent by the card when a READ_OCR command is received.
        # The structure of the first (MSB) byte is identical to response type R1.
        # The other four bytes contain the OCR register.

        # Get a MISO bit corresponding to a bit in the response.
        # self.read_bits is a list of MISO bytes, where each byte is a list of 8 bits,
        # Returns a MISO bit, which is a list containing [bit, ss, es].
        def get_bit(b):
            byte = (5 - (b // 8)) - 1 # 5 bytes in R3 response, -1 for zero indexing
            bit  = b % 8
            return self.read_bits[byte][bit]

        # Decode the R3 response.
        self.es_cmd = self.es
        self.read_buf.append(res)
        self.read_bits.append(self.miso_bits)
        if len(self.read_buf) == 1:
            self.handle_response_r1(res)
            self.putx([Ann.R3, ['R1: 0x%02x' % res]])
        elif len(self.read_buf) < 5:
            pass
        else:
            r1 = self.read_buf[0]
            ocr = (self.read_buf[1] << 24) | (self.read_buf[2] << 16) | (self.read_buf[3] << 8) | self.read_buf[4]
            self.putx([Ann.R3, ['R3: [R1: 0x%02x, OCR: 0x%08x]' % (r1, ocr)]])

            # Bit 31: 'Card power up status bit (busy)'.
            # This bit is set to LOW if the card has not finished the power up routine.
            power_up_status, self.ss_bit, self.es_bit = get_bit(31)
            s = '' if power_up_status else 'not '
            self.putb([Ann.BIT, ['Card has %sfinished the power up routine' % s]])

            # Bit 30: 'Card Capacity Status (CCS)'.
            # This bit is valid only when the card power up status bit is set.
            bit, self.ss_bit, self.es_bit = get_bit(30)
            s = 'unknown'
            if power_up_status:
                s = 'SDHC or SDXC' if bit else 'SDSC'
            self.putb([Ann.BIT, ['Card capacity is %s' % s]])

            # Bit 29: 'UHS-II Card Status'.
            uhs2, self.ss_bit, self.es_bit = get_bit(29)
            s = '' if uhs2 else 'not '
            self.putb([Ann.BIT, ['UHS-II interface %ssupported' % s]])

            # Bit 28: 'reserved'.
            _, self.ss_bit, self.es_bit = get_bit(28)
            self.putb([Ann.BIT, ['Reserved']])

            # Bit 27: 'Over 2TB support Status (CO2T)'.
            # Only SDUC card supports this bit.
            # TODO: unclear exactly how to determine if SDUC.
            bit, self.ss_bit, self.es_bit = get_bit(27)
            s = '' if bit else 'not '
            self.putb([Ann.BIT, ['Over 2TB is %ssupported' % s]])

            # Bits 26..25: 'reserved'.
            self.ss_bit, self.es_bit = get_bit(26)[1], get_bit(25)[2]
            self.putb([Ann.BIT, ['Reserved']])

            # Bit 24: 'Switching to 1.8V Accepted (S18A)'.
            bit, self.ss_bit, self.es_bit = get_bit(24)
            s = '' if bit else 'not '
            self.putb([Ann.BIT, ['Switching to 1.8V %saccepted' % s]])

            # Bits 23..0 are interpreted differently depending upon whether a UHS-II card or not.
            if uhs2:
                # TODO
                pass
            else:
                # Bits 23..15 represent the supported voltage ranges.
                voltages = {
                    15: '2.7-2.8',
                    16: '2.8-2.9',
                    17: '2.9-3.0',
                    18: '3.0-3.1',
                    19: '3.1-3.2',
                    20: '3.2-3.3',
                    21: '3.3-3.4',
                    22: '3.4-3.5',
                    23: '3.5-3.6',
                }
                for k, v in voltages.items():
                    bit, self.ss_bit, self.es_bit = get_bit(k)
                    s = '' if bit else 'not '
                    self.putb([Ann.BIT, ['%sV %ssupported' % (v, s)]])

                # Bits 14..8: 'reserved'.
                self.ss_bit, self.es_bit = get_bit(14)[1], get_bit(8)[2]
                self.putb([Ann.BIT, ['Reserved']])

                # Bit 7: 'Reserved for Low Voltage Range'
                # Physical Layer Simplified Specification Version 9.00 does not define how to interpret this.
                _, self.ss_bit, self.es_bit = get_bit(7)
                self.putb([Ann.BIT, ['Reserved']])

                # Bits 6..0: 'reserved'.
                self.ss_bit, self.es_bit = get_bit(6)[1], get_bit(0)[2]
                self.putb([Ann.BIT, ['Reserved']])

            self.state = 'IDLE'
            self.read_buf = []
            self.read_bits = []

    # Note: Response token formats R4 and R5 are reserved for SDIO.

    # TODO: R6?

    def handle_response_r7(self, res):
        # The R7 response token format (5 bytes).
        # Sent by the card when a SEND_IF_COND command (CMD8) is received.
        # The structure of the first (MSB) byte is identical to response type R1.
        # The other four bytes contain the card operating voltage information and 
        # echo back of check pattern in argument and are specified by the same definition
        # as R7 response in SD mode.
        # Bits 31..28 are listed as "command version" but not clearly defined.

        # Get a MISO bit corresponding to a bit in the response.
        # self.read_bits is a list of MISO bytes, where each byte is a list of 8 bits,
        # Returns a MISO bit, which is a list containing [bit, ss, es].
        def get_bit(b):
            byte = (5 - (b // 8)) - 1 # 5 bytes in R7 response, -1 for zero indexing
            bit  = b % 8
            return self.read_bits[byte][bit]

        # Decode the value for bits 11..8 "voltage accepted" to a string.
        def decode_voltage(value):
            strings = {
                0: "Not Defined",
                1: "2.7-3.6V",
                2: "Reserved for Low Voltage Range",
                4: "Reserved",
                8: "Reserved"
            }
            return strings.get(value, "Not Defined")

        # Decode the R7 response.
        self.es_cmd = self.es
        self.read_buf.append(res)
        self.read_bits.append(self.miso_bits)
        if len(self.read_buf) == 1:
            self.handle_response_r1(res)
            self.putx([Ann.R7, ['R1: 0x%02x' % res]])
        elif len(self.read_buf) < 5:
            pass
        else:
            r1 = self.read_buf[0]
            version = self.read_buf[0] >> 4
            voltage = decode_voltage(self.read_buf[3])
            pattern = self.read_buf[4]
            self.putx([Ann.R7, ['R7: [R1: 0x%02x, Command Version: 0x%01x, Voltage Accepted: %s, Check Pattern: 0x%02x]' % (r1, version, voltage, pattern)]])

            # Bits 31..28: 'command version'
            self.ss_bit, self.es_bit = get_bit(31)[1], get_bit(28)[2]
            self.putb([Ann.BIT, ['Command Version: 0x%01x' % version]])

            # Bits 27..12: 'reserved bits'
            self.ss_bit, self.es_bit = get_bit(27)[1], get_bit(12)[2]
            self.putb([Ann.BIT, ['Reserved']])

            # Bits 11..8: 'voltage accepted'
            self.ss_bit, self.es_bit = get_bit(11)[1], get_bit(8)[2]
            self.putb([Ann.BIT, ['Voltage Accepted: %s' % voltage]])

            # Bits 7..0: 'check pattern'
            self.ss_bit, self.es_bit = get_bit(7)[1], get_bit(0)[2]
            self.putb([Ann.BIT, ['Check Pattern: 0x%02x' % pattern]])

            self.state = 'IDLE'
            self.read_buf = []
            self.read_bits = []

    def handle_data_cmd17(self, miso):
        # CMD17 returns one byte R1, then some bytes 0xff, then a Start Block
        # (single byte 0xfe), then self.blocklen bytes of data, then always
        # 2 bytes of CRC.
        if self.cmd17_start_token_found:
            if len(self.read_buf) == 0:
                self.ss_data = self.ss
                if not self.blocklen:
                    # Assume a fixed block size when inspection of the previous
                    # traffic did not provide the respective parameter value.
                    # TODO: Make the default block size a PD option?
                    self.blocklen = 512
            self.read_buf.append(miso)
            # Wait until block transfer completed.
            if len(self.read_buf) < self.blocklen:
                return
            if len(self.read_buf) == self.blocklen:
                self.es_data = self.es
                self.put(self.ss_data, self.es_data, self.out_ann, [Ann.CMD17, ['Block data: %s' % self.read_buf]])
            elif len(self.read_buf) == (self.blocklen + 1):
                self.ss_crc = self.ss
            elif len(self.read_buf) == (self.blocklen + 2):
                self.es_crc = self.es
                # TODO: Check CRC.
                self.put(self.ss_crc, self.es_crc, self.out_ann, [Ann.CMD17, ['CRC']])
                self.state = 'IDLE'
                self.read_buf = []
                self.cmd17_start_token_found = False
                self.is_cmd17 = False
        elif miso == 0xfe:
            self.put(self.ss, self.es, self.out_ann, [Ann.CMD17, ['Start Block']])
            self.cmd17_start_token_found = True

    def handle_data_cmd24(self, mosi):
        if self.cmd24_start_token_found:
            if len(self.read_buf) == 0:
                self.ss_data = self.ss
                if not self.blocklen:
                    # Assume a fixed block size when inspection of the
                    # previous traffic did not provide the respective
                    # parameter value.
                    # TODO Make the default block size a user adjustable option?
                    self.blocklen = 512
            self.read_buf.append(mosi)
            # Wait until block transfer completed.
            if len(self.read_buf) < self.blocklen:
                return
            self.es_data = self.es
            self.put(self.ss_data, self.es_data, self.out_ann, [Ann.CMD24, ['Block data: %s' % self.read_buf]])
            self.state = 'DATA RESPONSE'
            self.read_buf = []
            self.cmd24_start_token_found = False
        elif mosi == 0xfe:
            self.put(self.ss, self.es, self.out_ann, [Ann.CMD24, ['Start Block']])
            self.cmd24_start_token_found = True

    def handle_data_response(self, miso):
        # Data Response token (1 byte).
        #
        # Format:
        #  - Bits[7:5]: Don't care.
        #  - Bits[4:4]: Always 0.
        #  - Bits[3:1]: Status.
        #    - 010: Data accepted.
        #    - 101: Data rejected due to a CRC error.
        #    - 110: Data rejected due to a write error.
        #  - Bits[0:0]: Always 1.
        miso &= 0x1f
        if miso & 0x11 != 0x01:
            # This is not the byte we are waiting for.
            # Should we return to IDLE here?
            return
        m = self.miso_bits
        self.put(m[7][1], m[5][2], self.out_ann, [Ann.BIT, ['Don\'t care']])
        self.put(m[4][1], m[4][2], self.out_ann, [Ann.BIT, ['Always 0']])
        if miso == 0x05:
            self.put(m[3][1], m[1][2], self.out_ann, [Ann.BIT, ['Data accepted']])
        elif miso == 0x0b:
            self.put(m[3][1], m[1][2], self.out_ann, [Ann.BIT, ['Data rejected (CRC error)']])
        elif miso == 0x0d:
            self.put(m[3][1], m[1][2], self.out_ann, [Ann.BIT, ['Data rejected (write error)']])
        self.put(m[0][1], m[0][2], self.out_ann, [Ann.BIT, ['Always 1']])
        cls = Ann.CMD24 if self.is_cmd24 else None
        if cls is not None:
            self.put(self.ss, self.es, self.out_ann, [cls, ['Data Response']])
        if self.is_cmd24:
            # We just send a block of data to be written to the card,
            # this takes some time.
            self.state = 'WAIT WHILE CARD BUSY'
            self.busy_first_byte = True
        else:
            self.state = 'IDLE'

    def wait_while_busy(self, miso):
        if miso != 0x00:
            cls = Ann.CMD24 if self.is_cmd24 else None
            if cls is not None:
                self.put(self.ss_busy, self.es_busy, self.out_ann, [cls, ['Card is busy']])
                self.is_cmd24 = False
            self.state = 'IDLE'
            return
        else:
            if self.busy_first_byte:
                self.ss_busy = self.ss
                self.busy_first_byte = False
            else:
                self.es_busy = self.es

    def decode(self, ss, es, data):
        # Packet.
        ptype, data1, data2 = data
        if ptype == 'DATA':
            # 'DATA': <data1> contains the MOSI data, <data2> contains the MISO data.
            # The data is _usually_ 8 bits (but can also be fewer or more bits).
            # Both data items are Python numbers (not strings), or None if the respective
            mosi, miso = data1, data2
            self.ss, self.es = ss, es

            # Decode via the state machine below.
            pass
        elif ptype == 'BITS':
            # 'BITS': <data1>/<data2> contain a list of bit values in this MOSI/MISO data
            # item, and for each of those also their respective start-/endsample numbers.

            # Store the individual bit values and ss/es numbers. The next packet
            # is guaranteed to be a 'DATA' packet belonging to this 'BITS' one.
            self.mosi_bits, self.miso_bits = data1, data2
            return
        elif ptype == 'CS-CHANGE':
            # 'CS-CHANGE': <data1> is the old CS# pin value, <data2> is the new value.
            # Both data items are Python numbers (0/1), not strings. At the beginning of
            # the decoding a packet is generated with <data1> = None and <data2> being the
            # initial state of the CS# pin or None if the chip select pin is not supplied.

            # Reset state when CS is asserted.
            if data2 == 0:
                self.state = 'IDLE'
                self.read_buf = []
                self.read_bits = []
                self.is_cmd17 = False
                self.cmd17_start_token_found = False
                self.is_cmd24 = False
                self.cmd24_start_token_found = False
                self.busy_first_byte = False
            return
        else:
            # 'TRANSFER': <data1>/<data2> contain a list of Data() namedtuples for each
            # byte transferred during this block of CS# asserted time. Each Data() has
            # fields ss, es, and val.

            # Currently unused.
            return

        # State machine.
        if self.state == 'IDLE':
            # Ignore stray 0xff bytes, some devices seem to send those!?
            if mosi == 0xff: # TODO?
                return
            self.state = 'GET COMMAND TOKEN'
            self.handle_command_token(mosi, miso)
        elif self.state == 'GET COMMAND TOKEN':
            self.handle_command_token(mosi, miso)
        elif self.state.startswith('HANDLE CMD'):
            self.miso, self.mosi = miso, mosi
            # Call the respective handler method for the command.
            a, cmdstr = 'a' if self.is_acmd else '', self.state[10:].lower()
            handle_cmd = getattr(self, 'handle_%scmd%s' % (a, cmdstr))
            handle_cmd()
            self.cmd_token = []
            self.cmd_token_bits = []
            # Leave ACMD mode again after the first command after CMD55.
            if self.is_acmd and cmdstr != '55':
                self.is_acmd = False
        elif self.state == 'GET RESPONSE R2':
            self.handle_response_r2(miso)
        elif self.state == 'GET RESPONSE R3':
            self.handle_response_r3(miso)
        elif self.state == 'GET RESPONSE R7':
            self.handle_response_r7(miso)
        elif self.state.startswith('GET RESPONSE'):
            # Ignore stray 0xff bytes, some devices seem to send those!?
            if miso == 0xff: # TODO?
                return
            # Call the respective handler method for the response.
            # Assume return to IDLE state, but allow response handlers
            # to advance to some other state when applicable.
            s = 'handle_response_%s' % self.state[13:].lower()
            handle_response = getattr(self, s)
            self.state = 'IDLE'
            handle_response(miso)
        elif self.state == 'HANDLE DATA BLOCK CMD17':
            self.handle_data_cmd17(miso)
        elif self.state == 'HANDLE DATA BLOCK CMD24':
            self.handle_data_cmd24(mosi)
        elif self.state == 'DATA RESPONSE':
            self.handle_data_response(miso)
        elif self.state == 'WAIT WHILE CARD BUSY':
            self.wait_while_busy(miso)
