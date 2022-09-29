#!/usr/bin/env python
#
# Scan through a digital rf recording
#
import numpy as n
import chirp_det as cd
import chirp_config as cc
import digital_rf as drf
from mpi4py import MPI
import time
import sys
import traceback

comm = MPI.COMM_WORLD
size = comm.Get_size()
rank = comm.Get_rank()


def scan_for_chirps(conf, cfb, block0=None):
    data = drf.DigitalRFReader(conf.data_dir)
    
    sample_rate, center_freq = get_metadata(data, conf.channel)
    bounds = data.get_bounds(conf.channel)

    if block0 == None:
        block0 = int(n.ceil(bounds[0]/(conf.n_samples_per_block*conf.step)))

    block1 = int(n.floor(bounds[1]/(conf.n_samples_per_block*conf.step)))

    # mpi scan through dataset
    for block_idx in range(block0, block1):
        #print('block_idx: %i' % block_idx)
        if block_idx % size == rank:
            # this is my block!
            try:
                cput0 = time.time()
                # we may skip over data (step > 1) to speed up detection
                i0 = block_idx * conf.n_samples_per_block * conf.step
                #            i0=block_idx*conf.n_samples_per_block*conf.step + idx0
                # read vector from recording
                z = data.read_vector_c81d(
                    i0, conf.n_samples_per_block, conf.channel)
                snrs, chirp_rates, f0s = cfb.seek(z, i0)
                cput1 = time.time()
                analysis_time = (conf.n_samples_per_block * conf.step) / sample_rate
                print("%d/%d Analyzing %s speed %1.2f * realtime" % (
                    rank, size, cd.unix2datestr(i0/conf.sample_rate), size*analysis_time/(cput1-cput0),
                ))
            except:
                print("error")
                traceback.print_exc()
    return(block1)


def get_metadata(data, channel):
    # pull SR and centerfreq from file to avoid errors
    meta = data.get_digital_metadata(channel).read()
    meta = meta[next(iter(meta.keys()))]
    sample_rate = meta['receiver']['samp_rate']
    center_freq = meta['receiver']['center_freq']
    return sample_rate, center_freq


if __name__ == "__main__":
    if len(sys.argv) == 2:
        conf = cc.chirp_config(sys.argv[1])
    else:
        conf = cc.chirp_config()

    cfb = cd.chirp_matched_filter_bank(conf)

    if not conf.realtime:
        scan_for_chirps(conf, cfb)
    else:
        block1 = None
        while True:
            block1 = scan_for_chirps(conf, cfb, block1)
            time.sleep(0.001)
