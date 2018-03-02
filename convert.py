#!/usr/bin/env python

'''
Convert `trace` file into `u-boot` c structures
'''


import sys
import os
from os.path import basename, splitext
import getopt
from collections import namedtuple
import re
import shutil

# architectures
ARCH_ALL, ARCH_5600, ARCH_AXC_6700 = range(200, 203)

# engine types
EIOA, EIOAE, MME, NCA, PBM, VP = range(6)

# processing state
(EIOA_LOOK, EIOAE_LOOK, MME_LOOK, NCA_LOOK, PBM_LOOK, VP_LOOK)  = range(9, 15)
(EIOA_FOUND, EIOAE_FOUND, MME_FOUND, NCA_FOUND, PBM_FOUND, VP_FOUND) = range(30, 36)

ES = namedtuple('EngineSpec', 'start_state, start_sign, end_state, end_sign, output_file')

# enigines in order of processing
ENGINES = [MME, PBM, VP, NCA, EIOA, EIOAE]
ENGINE_ALL = 100  # for dumping all engines

# engines definitions
ENGINE_DEFS = {
  EIOA  : ES(EIOA_LOOK, '# Begin: Engines.EIOA', EIOA_FOUND, '# End:   Engines.EIOA', 'eioa'),
  EIOAE : ES(EIOAE_LOOK,'# Begin: EIOA Port(s) Enable', EIOAE_FOUND, '# End:   EIOA Port(s) Enable', 'eioa'),
  MME   : ES(MME_LOOK, '# Begin: Engines.MME', MME_FOUND, '# End:   Engines.MME', 'mme'),
  PBM   : ES(PBM_LOOK,'# Begin: PBM', PBM_FOUND, '# End:   PBM', 'pbm'),
  VP    : ES(VP_LOOK, '# Begin: VirtualPipelines', VP_FOUND, '# End:   VirtualPipelines', 'vp'),
  NCA   : ES(NCA_LOOK, '# Begin: Engines.NCA', NCA_FOUND, '# End:   Engines.NCA', 'nca')
}

ARCH_TO_ID = {
    '5600'    :  ARCH_5600,
    '6700'    :  ARCH_AXC_6700,
    'all'     :  ARCH_ALL
}

# architecture determines processing
ARCH_TO_STATE = {
    ARCH_5600     :  MME_LOOK,
    ARCH_AXC_6700 :  MME_LOOK,
    ARCH_ALL      :  MME_LOOK
}

CONVERTER_TAG_ADD = '# XXX generated by converter\n'
CONVERTER_TAG_MOD = '# XXX modified by converter'
CLOCK_PAD_ADDR = '0.273.0.0x000000004c'

# commandline global options
VERBOSE = False
REF_USE_PAD = None

STATE_TO_STR = {
    EIOA_LOOK    :  'EIOA_LOOK',
    EIOAE_LOOK   :  'EIOAE_LOOK',
    MME_LOOK     :  'MME_LOOK',
    NCA_LOOK     :  'NCA_LOOK',
    PBM_LOOK     :  'PBM_LOOK',
    VP_LOOK      :  'VP_LOOK',

    EIOA_FOUND   :  'EIOA_FOUND',
    EIOAE_FOUND  :  'EIOAE_FOUND',
    MME_FOUND    :  'MME_FOUND',
    NCA_FOUND    :  'NCA_FOUND:',
    PBM_FOUND    :  'PBM_FOUND:',
    VP_FOUND     :  'VP_FOUND'
}

def cutout_trace_file(filename, out_filename):
	#engines used to produce trace file
	#everything outside selected engines # BEGIN and #END block will be removed"
    needed = ["Engines.MME","PBM","VirtualPipelines","Engines.NCAv3","Engines.EIOA","EIOA Port(s) Enable"]
    f_in = open(filename,"r")
    f_out = open(out_filename,"w")
    cur_tag = None;
    for line in f_in:
        if (cur_tag == None and line.find("Begin:") != -1):
            for need in needed:
                if (re.match(r".*" + re.escape(need) + r"\s*\Z",line)):
                    tmp = line[line.find("Begin:"):];
                    tmp = tmp[len("Begin: "):]
                    cur_tag = tmp
        if (cur_tag != None):
            f_out.write(line)
        if (cur_tag != None):
            if (line.find("End:") != -1):
                if (re.match(r".*" + re.escape(cur_tag) + r"\s*",line)):
                    cur_tag = None


def c_comment(line, msg=''):
    ''' Format C comment '''
    line = line.strip(' #\n')  # remove hash, whitespaces and newline
    fmtmsg = ''
    if line:
        if msg:
            fmtmsg = '\n\t/* {0} {1}  */\n'.format(msg, line)
        else:
            fmtmsg = '\n\t/* {0} */\n'.format(line)
    return fmtmsg


def save_file(fname, tracelst):
    with open(fname, 'w') as f:
        f.write(''.join(tracelst))


def save_trace(trace, arch, parsed_data):
    ''' Save traces to separate files per engine '''
    for engine_type in trace:
        engine = ENGINE_DEFS[engine_type]
        save_file('{0}.trace'.format(engine.output_file), trace[engine_type])
        convert('{0}.c'.format(engine.output_file), trace[engine_type], parsed_data)

    # special case for EIOE, it needs to be appended to EIOA
    if EIOA in trace and EIOAE in trace:
        engine = ENGINE_DEFS[EIOA]
        # append "EIOA Port(s) Enable" to EIOA
        trace[EIOA] += trace[EIOAE]
        save_file('{0}.trace'.format(engine.output_file), trace[EIOA])
        convert('{0}.c'.format(engine.output_file), trace[EIOA], parsed_data)


def trace_append(trace, line, engine_type):
    if not engine_type in trace:
        trace[engine_type] = []
    trace[engine_type].append(line)


def parse_trace(fname, arch):
    '''Parse trace file and return dictionary with list
    of lines for every engine'''

    trace = {}
    engine_type = ENGINES.pop(0)
    engine = ENGINE_DEFS[engine_type]
    state = engine.start_state
    print 'parse trace'
    with open(fname) as f:
        for line in f:
            if not line.rstrip(): continue   # skip empty lines

            if VERBOSE:
                print 'state {0}: {1}'.format(STATE_TO_STR[state], line),

            if state == engine.start_state:
                if line.startswith(engine.start_sign):
                    if engine_type == NCA:
                        # discard "# Begin: Engines.NCAv3 (CPU)"
                        if len(line.split()) == 3:
                            state = engine.end_state
                            trace_append(trace, line, engine_type)
                    else:
                        state = engine.end_state
                        trace_append(trace, line, engine_type)
            elif state == engine.end_state:
                if line.startswith(engine.end_sign):
                    trace_append(trace, line, engine_type)
                    try:
                        engine_type = ENGINES.pop(0)
                        engine = ENGINE_DEFS[engine_type]
                        state = engine.start_state
                    except IndexError:
                        break
                else:
                    trace_append(trace, line, engine_type)
            else:
                print 'Unknown processing state <{0}> for line <{1}>'.format(state, line)
                break

    return trace


def parse_all(fname):
    '''Parse trace file and return dictionary with list
    of lines for every engine'''

    print 'parse all'
    trace = {ENGINE_ALL: []}
    with open(fname) as f:
        for line in f:
            if not line.rstrip(): continue   # skip empty lines
            if VERBOSE:
                print '{0}'.format(line),
            trace[ENGINE_ALL].append(line)
    return trace


def save_all(trace, parsed_data):
    ''' Save traces to separate files per engine '''
    save_file('all.trace', trace[ENGINE_ALL])
    convert('all.c', trace[ENGINE_ALL], parsed_data)


def pad_clock(values, node, target, offset, use_pad):
    ''' Enable/Disable PAD clock '''
    new_values = list(values)
    if node == 273 and target == 0:
        if offset == 0x000000004c:
            clockval = int(new_values[0], 16)
            if use_pad:
                # enable PAD clock
                clockval |= ( 1 << 1)
            else:
                # disable PAD clock
                clockval &= ~( 1 << 1)
            new_values[0] = hex(clockval).rstrip('L')   # strip 'L' from long integer
    return new_values


def write_values(f, newlst, oldlst, node, target, offset):
    ''' Write values to file '''
    for newval, oldval in zip(newlst, oldlst):
        newval = int(newval, 16)
        oldval = int(oldval, 16)

        if newval != oldval:
            # write old value commented out
            f.write(c_comment('\t{{NCR_COMMAND_WRITE, NCP_REGION_ID({0}, {1}), 0x{2:08x}, 0x{3:08x}, 0}},\n'\
                              .format(node, target, offset, oldval), CONVERTER_TAG_MOD))
            if VERBOSE: print 'CHANGE: node: {0}, target: {1}, offset: {2:08x}, old: {3:08x}, new: {4:08x}'\
               .format(node, target, offset, oldval, newval)
        # write new value
        f.write('\t{{NCR_COMMAND_WRITE, NCP_REGION_ID({0}, {1}), 0x{2:08x}, 0x{3:08x}, 0}},\n'\
                .format(node, target, offset, newval))
        offset += 4


def convert_ncp_write(f, options, parsed_data):
    ''' Handle ncpWrite command '''
    m = re.search(r'([^\.]*)\.([^\.]*)\.([^\.]*)\.([^\s]*)\s+(.*)', options)
    if not m:
        print 'Unable to parse <{0}>'.format(options)
        return

    node = int(m.group(2))
    target = int(m.group(3))
    offset = int(m.group(4), 16)
    values = m.group(5).split(' ')
    values = [v for v in values if v]  # filter out empty values
    skip = False
    if target == 16 and node in (23, 31):
        skip = offset in (160, 164, 168, 172, 176, 180, 184, 188)

    if target == 18 and node in (23, 31):
        skip = offset in (772, 776, 836, 840, 844, 848, 852, 964, 968,
                          1028, 1032, 1036, 1040, 1044, 1156, 1160, 1220,
                          1224, 1228, 1232, 1236, 1348, 1352, 1412, 1416,
                          1420, 1424, 1428)
    if not skip:
        new_values = values
        if not REF_USE_PAD is None:
            new_values = pad_clock(new_values, node, target, offset, REF_USE_PAD)
        write_values(f, new_values, values, node, target, offset)


def convert_ncp_read(f, options, parsed_data):
    ''' Handle ncpRead command '''
    m = re.search(r'([^\.]*)\.([^\.]*)\.([^\.]*)\.([^\s]*)', options)
    if not m:
        print 'Unable to parse <{0}>'.format(options)
        return

    node = int(m.group(2))
    target = int(m.group(3))
    offset = int(m.group(4), 16)

    f.write('\t{NCR_COMMAND_USLEEP, 0, 0, 1000, 0},\n')
    f.write('\t{{NCR_COMMAND_READ, NCP_REGION_ID({0}, {1}), 0x{2:08x}, 0, 0}},\n'\
            .format(node, target, offset))


def convert_ncp_modify(f, options, parsed_data):
    ''' Handle ncpModify command '''
    m = re.search(r'([^\.]*)\.([^\.]*)\.([^\.]*)\.([^\s]*)\s+([\S]*)\s+([\S]*)', options)
    if not m:
        print 'Unable to parse <{0}>'.format(options)
        return

    node = int(m.group(2))
    target = int(m.group(3))
    offset = int(m.group(4), 16)
    mask = int(m.group(5), 16)
    value = int(m.group(6), 16)

    f.write('\t{{NCR_COMMAND_MODIFY, NCP_REGION_ID({0}, {1}), 0x{2:08x}, 0x{3:08x}, 0x{4:08x}}},\n'\
            .format(node, target, offset, value, mask))


def convert_ncp_usleep(f, options, parsed_data):
    ''' Handle ncpUsleep command '''
    f.write('\t{{NCR_COMMAND_USLEEP, 0, 0, {0}, 0}},\n'.format(options))


def convert_ncp_poll(f, options, parsed_data):
    ''' Handle ncpPoll command '''
    m = re.search(r'''\-l\s+([\S]+)\s+                            # Loops
                      \-t\s+([\S]+)\s+                            # Timeout
                      ([^\.]*)\.([^\.]*)\.([^\.]*)\.([^\.]*)\s+   # d.n.t.o
                      ([\S]+)\s+                                  # Mask
                      ([\S]+)                                     # Value''',
                  options, re.VERBOSE)
    if not m:
        print 'Unable to parse <{0}>'.format(options)
        return

    node = int(m.group(4))
    target = int(m.group(5))
    offset = int(m.group(6), 16)
    mask = int(m.group(7), 16)
    value = int(m.group(8), 16)

    f.write('\t{{NCR_COMMAND_POLL, NCP_REGION_ID({0}, {1}), 0x{2:08x}, 0x{3:08x}, 0x{4:08x}}},\n'\
            .format(node, target, offset, mask, value))


def convert(outfile, inlst, parsed_data):
    handlers = {
        'ncpWrite'    :  convert_ncp_write,
        'ncpRead'     :  convert_ncp_read,
        'ncpModify'   :  convert_ncp_modify,
        'ncpUsleep'   :  convert_ncp_usleep,
        'ncpPoll'     :  convert_ncp_poll
    }
    with open(outfile, 'w') as f:
        f.write('static ncr_command_t {0}[] = {{\n'.format(splitext(outfile)[0]))
        for line in inlst:
            if not line: continue    # skip empty lines
            if line.startswith('#'):
                line = c_comment(line)
                if line:
                    f.write(line)
                continue
            m = re.search(r'([\w]+)\s+(.*)', line)
            if m:
                command, options = m.group(1), m.group(2)
                try:
                    handlers[command](f, options, parsed_data)
                except KeyError:
                    if VERBOSE: print 'Unknown command <{0}> skipped.'.format(command)
            else:
                print 'Unable to parse <{0}>'.format(line)
                break

        # footer
        f.write('\t{NCR_COMMAND_NULL, 0, 0, 0, 0}\n')
        f.write('};\n')



def parse_tree(fname):
    ''' Get values from tree file as dictionary:
          - list of `SharedMemoryPool` `physicalBaseAddress`s
          - maxDynamic
    '''
    def parse_val(s):
        v = s.split()[-1]             # get last part of line
        v = hex(int(v)).rstrip('L')   # strip 'L' from long integer
        return v

    smp_found = False
    block_found = False
    maxd = 0
    smp_ba_pa = []
    return {'phy_addr': smp_ba_pa, 'maxd': maxd}


def sanity(input_file, tree_file):
    err = 0

    # check input file
    f = None
    try:
        f = open(input_file)
    except Exception as ex:
        print 'Couldn\'t open file <{0}>: {1}'.format(input_file, ex)
        err += 1
    else:
        f.close()


    return err


def usage(prog):
    print 'Usage: {0} -i <input_file> -a <architecture> [-r 0|1] [-v]'.format(basename(prog))
    print '\t -i, --input          -   input trace file'
    print '\t -a, --architecture   -   architecture ({0})'.format(','.join(sorted(ARCH_TO_ID.keys())))
    print '\t -r, --ref_use_pad    -   0 (disable) or 1 (enable) PAD clock ({0})'.format(CLOCK_PAD_ADDR)
    print '\t -v, --verbose        -   enable verbose mode'
    sys.exit(2)

def parse_args():
    try:
        opts, args = getopt.getopt(sys.argv[1:], 'a:hi:r:v',
                                   ['architecture=', 'help', 'input=', 'ref_use_pad=',  'verbose'])
    except getopt.GetoptError as err:
        # print help information and exit:
        print str(err) # will print something like "option -a not recognized"
        usage(sys.argv[0])
    arch = None
    ref_use_pad = None
    input_ = None
    verbose = False
    for o, a in opts:
        if o in ('-v', '--verbose'):
            verbose = True
        elif o in ('-h', '--help'):
            usage(sys.argv[0])
        elif o in ('-a', '--architecture'):
            arch = a
        elif o in ('-i', '--input'):
            input_ = a
        elif o in ('-r', '--ref_use_pad'):
            ref_use_pad = int(a)
        else:
            assert False, 'unhandled option'

    if not input_:
        print 'Provide input file for parsing'
        usage(sys.argv[0])

    if not arch:
        print 'Provide target architecture'
        usage(sys.argv[0])

    try:
        arch = ARCH_TO_ID[arch]
    except KeyError:
        print 'Unknown architecture <{0}>'.format(arch)
        sys.exit(4)

    return input_, arch, ref_use_pad, verbose


def main():
    global REF_USE_PAD, VERBOSE

    input_, arch, REF_USE_PAD, VERBOSE = parse_args()
    input_out = input_ + "_hwio"
    input_out_all = input_ + "_hwio_all"

    f_in = open(input_,"r")
    f_out = open(input_out_all,"w")
    for line in f_in:
        f_out.write(line)

    cutout_trace_file(input_, input_out)

    tree_file = input_ + '_tree'
    if sanity(input_out, tree_file):
        sys.exit(3)
    parsed_data = parse_tree(tree_file)
    if VERBOSE: print 'PARSED DATA: PHY_ADDR: {phy_addr}, MAXD: {maxd}'.format(**parsed_data)

    trace = None
    trace = parse_all(input_out)
    save_all(trace, parsed_data)

    trace = None
    trace = parse_all(input_out_all)
    #save_all(trace, parsed_data)


if __name__ == '__main__':
    main()
