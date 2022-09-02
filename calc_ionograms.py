#!/usr/bin/env python
#
# Scan through a digital rf recording
#
import numpy as np
import digital_rf as drf
from mpi4py import MPI
import glob
import scipy.signal as ss
import scipy.constants as c
import h5py
import chirp_config as cc
import chirp_det as cd
import matplotlib.pyplot as plt
import time
import os
import sys
import traceback
import shutil
import multiprocessing as mp
from pathlib import Path

# c library
import chirp_lib as cl

comm = MPI.COMM_WORLD
size = comm.Get_size()
rank = comm.Get_rank()


def get_m_per_Hz(rate):
    """
    Determine resolution of a sounding.
    """
    # rate = [Hz/s]
    # 1/rate = [s/Hz]
    dt = 1.0/rate
    # m/Hz round trip
    return(dt*c.c/2.0)


def spectrogram(x, window=1024, step=512, wf=ss.hann(1024)):
    n_spec = int((len(x)-window)/step)
    S = np.zeros([n_spec, window])
    for i in range(n_spec):
        S[i, ] = np.abs(np.fft.fftshift(
            np.fft.fft(wf*x[(i*step):(i*step+window)])))**2.0
    return(S)


def copy_data_files(conf, copy_q, move_q):
    """
    When directed by the copy queue, copy files from a digital_rf location to
    another location (temp location) and direct another process via the move
    queue to move the files to a different location (permanent location).
    """
    # Prepare the staging directory
    staging_path = Path(conf.data_staging_dir, str(rank))
    staging_path.mkdir(parents=True, exist_ok=True)

    # Process filenames from the queue until a stop
    while True:
        filename = copy_q.get()

        # We have received the end of the list, stop
        if filename == "":
            try:
                shutil.rmtree(staging_path)
            except OSError as why:
                print("Error: failed to delete staging path: " +
                      str(staging_path) + "; Reason: " + str(why))
            break

        # We need to create a string to the time directory that holds the IQ file
        # Example: <conf.data_dir>/<conf.channel>/2021-05-04T17-00-00/rf@1620150628.000.h5
        t0 = filename.split("/")[-1][3:].split(".")[0]
        time_dir = cd.unix2drfdirname(t0)

        data_filename_path = Path(
            conf.data_dir, conf.channel, time_dir, filename)

        try:
            shutil.copy2(str(data_filename_path), str(staging_path))
        except OSError as why:
            print("Error: failed to copy; Src: " + str(data_filename_path) +
                  "; Dst: " + str(staging_path) + "; Reason: " + str(why))

        # Signal to the move process to move the copied file
        move_q.put(str(Path(staging_path, filename)))


def move_data_files(conf, move_q):
    """ 
    When directed by the move queue, move files from one location to another.
    """
    while True:
        file_with_path = move_q.get()

        # We have received the end of the list, stop
        if file_with_path == "":
            break

        # Determine if we move or delete the files
        if conf.save_chirp_iq:
            t0 = file_with_path.split("/")[-1][3:].split(".")[0]
            output_path = Path(conf.output_dir, cd.unix2dirname(t0), "raw_iq")
            output_path.mkdir(parents=True, exist_ok=True)

            try:
                shutil.move(file_with_path, str(output_path))
            except OSError as why:
                # print("Error: failed to move; Src: " + file_with_path +
                #      "; Dst: " + str(output_path) + "; Reason: " + str(why))
                pass
        else:
            Path(file_with_path).unlink()


def chirp_downconvert(conf,
                      t0,
                      d,
                      i0,
                      ch,
                      rate,
                      dec=2500,
                      realtime_req=None,
                      cid=0,
                      copy_q=None):
    cput0 = time.time()
    sleep_time = 0.0
    sr = conf.sample_rate
    cf = conf.center_freq
    dur = conf.maximum_analysis_frequency/rate
    if realtime_req == None:
        realtime_req = dur
    idx = 0
    step = 1000
    n_windows = int(dur*sr/(step*dec))+1

    cdc = cl.chirp_downconvert(f0=-cf,
                               rate=rate,
                               dec=dec,
                               dt=1.0/conf.sample_rate,
                               n_threads=conf.n_downconversion_threads)

    zd_len = n_windows*step
    zd = np.zeros(zd_len, dtype=np.complex64)

    z_out = np.zeros(step, dtype=np.complex64)
    n_out = step

    data_filename_hist = []
    for fi in range(n_windows):
        missing = False
        try:
            if conf.realtime:
                b = d.get_bounds(ch)
                while ((i0+idx+step*dec+cdc.filter_len*dec)+int(conf.sample_rate)) > b[1]:
                    # wait for more data to be acquired
                    # as the tail of the buffer doesn't have he data we
                    # need yet
                    time.sleep(1.0)
                    sleep_time += 1.0
                    b = d.get_bounds(ch)

            z_a = d.read_vector_c81d(i0+idx, step*dec+cdc.filter_len*dec, ch)

            # Phase shift and add the second channel
            # z_b = d.read_vector_c81d(
            #    i0+idx, step*dec+cdc.filter_len*dec, "chb")
            # z_b = z_b * np.exp(1j * (np.pi / 2))
            # z = z_a + z_b
            z = z_a
        except:
            # z=np.zeros(step*dec+cdc.filter_len*dec,dtype=np.complex64)
            missing = True

        # we can skip this heavy step if there is missing data
        if not missing:
            # Get the name of each unique data file that is used to process the sounder
            data_filename = int((i0+idx)/conf.sample_rate)
            if len(data_filename_hist) == 0 or data_filename not in data_filename_hist:
                # print("Rank", rank, "; Reading file:", data_filename)
                cid_formatted = ".%03d.h5" % (cid)
                data_filename_full = "rf@" + str(data_filename) + cid_formatted
                copy_q.put(data_filename_full)

                data_filename_hist.append(data_filename)

            cdc.consume(z, z_out, n_out)
        else:
            # step chirp time forward
            cdc.advance_time(dec*step)
            z_out[:] = 0.0

        zd[(fi*step):(fi*step+step)] = z_out

        idx += dec*step

    dr = conf.range_resolution
    df = conf.frequency_resolution
    sr_dec = sr/dec
    ds = get_m_per_Hz(rate)
    fftlen = int(sr_dec*ds/dr/2.0)*2
    fft_step = int((df/rate)*sr_dec)

    S = spectrogram(np.conj(zd), window=fftlen,
                    step=fft_step, wf=ss.hann(fftlen))

    freqs = rate*np.arange(S.shape[0])*fft_step/sr_dec
    range_gates = ds*np.fft.fftshift(np.fft.fftfreq(fftlen, d=1.0/sr_dec))

    ridx = np.where(np.abs(range_gates) < conf.max_range_extent)[0]

    try:
        dname = "%s/%s" % (conf.output_dir, cd.unix2dirname(t0))
        if not os.path.exists(dname):
            os.mkdir(dname)
        ofname = "%s/lfm_ionogram-%03d-%1.2f.h5" % (dname, cid, t0)
        print("Writing to %s" % ofname)
        ho = h5py.File(ofname, "w")
        ho["S"] = S[:, ridx]          # ionogram frequency-range
        ho["freqs"] = freqs  # frequency bins
        ho["rate"] = rate    # chirp-rate
        ho["ranges"] = range_gates[ridx]
        ho["t0"] = t0
        ho["id"] = cid
        ho["sr"] = float(sr_dec)  # ionogram sample-rate
        if conf.save_raw_voltage:
            ho["z"] = zd
        ho["ch"] = ch            # channel name
        ho.close()
    except:
        traceback.print_exc(file=sys.stdout)
        print("error writing file")

    cput1 = time.time()
    cpu_time = cput1-cput0-sleep_time
    print("Done processed %1.2f s in %1.2f s, speed %1.2f * realtime" %
          (realtime_req, cpu_time, realtime_req/cpu_time))
    sys.stdout.flush()


def analyze_all(conf, d):
    fl = glob.glob("%s/*/par-*.h5" % (conf.output_dir))
    n_ionograms = len(fl)
    # mpi scan through the whole dataset
    for ionogram_idx in range(rank, n_ionograms, size):
        h = h5py.File(fl[ionogram_idx], "r")
        chirp_rate = np.copy(h[("chirp_rate")])
        t0 = np.copy(h[("t0")])
        i0 = np.int64(t0*conf.sample_rate)
        print("calculating i0=%d chirp_rate=%1.2f kHz/s t0=%1.6f" %
              (i0, chirp_rate/1e3, t0))
        h.close()

        chirp_downconvert(conf,
                          t0,
                          d,
                          i0,
                          conf.channel,
                          chirp_rate,
                          dec=2500)


def analyze_realtime(conf, d):
    """ 
    Realtime analysis using analytic timing
    We allocate one MPI process for each sounder to be on the safe side.

    TODO: load chirp timing information dynamically
          and use a process pool to calculate as many chirp ionograms 
          as there are computational resources.
    """
    st = conf.sounder_timings[rank]
    n_sounders = len(st)
    ch = conf.channel
    while True:
        b = d.get_bounds(ch)
        t0 = np.floor(np.float128(b[0]) / np.float128(conf.sample_rate))
        t1 = np.floor(np.float128(b[1]) / np.float128(conf.sample_rate))

        # find the next sounder that can be measured with shortest wait time
        best_sounder = 0
        best_wait_time = 1e6
        best_t0 = 0
        best_id = 0
        for s_idx in range(n_sounders):
            rep = np.float128(st[s_idx]["rep"])
            chirpt = np.float128(st[s_idx]["chirpt"])
            chirp_rate = st[s_idx]["chirp-rate"]
            cid = st[s_idx]["id"]

            try_t0 = rep*np.floor(t0/rep)+chirpt
            while try_t0 < t0:
                try_t0 += rep
            wait_time = try_t0-t0

            if wait_time < best_wait_time:
                best_sounder = s_idx
                best_t0 = try_t0
                best_wait_time = wait_time
                best_id = cid
        rep = np.float128(st[best_sounder]["rep"])
        chirpt = np.float128(st[best_sounder]["chirpt"])
        chirp_rate = st[best_sounder]["chirp-rate"]
        next_t0 = float(best_t0)
        print("Rank %d chirp id %d analyzing chirp-rate %1.2f kHz/s chirpt %1.4f rep %1.2f" %
              (rank, best_id, chirp_rate/1e3, chirpt, rep))
        i0 = int(next_t0*conf.sample_rate)
        realtime_req = conf.sample_rate/chirp_rate
        print("Buffer extent %1.2f-%1.2f launching next chirp at %1.2f %s" % (b[0]/conf.sample_rate,
                                                                              b[1] /
                                                                              conf.sample_rate,
                                                                              next_t0,
                                                                              cd.unix2datestr(next_t0)))

        chirp_downconvert(conf,
                          next_t0,
                          d,
                          i0,
                          conf.channel,
                          chirp_rate,
                          realtime_req=realtime_req,
                          dec=conf.decimation,
                          cid=best_id)


def get_next_chirp_par_file(conf, d):
    """ 
    wait until we encounter a parameter file with remaining time 
    """
    # find the next sounder that can be measured
    while True:
        ch = conf.channel
        b = d.get_bounds(ch)
        buffer_t0 = np.floor(np.float128(b[0])/np.float128(conf.sample_rate))
        while np.isnan(buffer_t0):
            b = d.get_bounds(ch)
            buffer_t0 = np.floor(np.float128(
                b[0])/np.float128(conf.sample_rate))
            # t1=np.floor(np.float128(b[1])/np.float128(conf.sample_rate))
            print("nan bounds for ringbuffer. trying again")
            time.sleep(1)

        # todo: look at today and yesterday. only looking
        # at today will result in a few lost ionograms
        # when the day is changing
        dname = "%s/%s" % (conf.output_dir,
                           cd.unix2dirname(conf.output_dir_time))
        fl = glob.glob("%s/par*.h5" % (dname))
        fl.sort()

        if len(fl) > 0:
            for fi in range(len(fl)):
                ftry = fl[len(fl)-fi-1]

                # proceed if this hasn't already been analyzed.
                if not os.path.exists("%s.done" % (ftry)):
                    h = h5py.File(ftry, "r")
                    t0 = float(np.copy(h[("t0")]))
                    i0 = np.int64(t0*conf.sample_rate)
                    chirp_rate = float(np.copy(h[("chirp_rate")]))
                    h.close()
                    t1 = conf.maximum_analysis_frequency/chirp_rate + t0

                    tnow = time.time()

                    # if the beginning of the buffer is before the end of the chirp,
                    # start analyzing as there is at least some of the the ionogram
                    # still in the buffer. the start of the buffer is
                    # before the the chirp ends
                    # t0 ---- t1
                    #      bt0-------bt1
                    if buffer_t0 < t1:
                        # if not already analyzed, analyze it
                        if not os.path.exists("%s.done" % (ftry)):
                            ho = h5py.File("%s.done" % (ftry), "w")
                            ho["t_an"] = time.time()
                            ho.close()
                            print("Rank %d analyzing %s time left in sweep %1.2f s" % (
                                rank, ftry, t1-tnow))
                            return(ftry)
                    else:
                        # we haven't analyzed this one, but we no longer
                        # can, because it is not in the buffer
                        print("Not able to analyze %s (%1.2f kHz/s), because it is no longer in the buffer. Buffer start at %1.2f and chirp ends at %1.2f" %
                              (ftry, chirp_rate/1e3, buffer_t0, t1))
                        ho = h5py.File("%s.done" % (ftry), "w")
                        ho["t_an"] = time.time()
                        ho.close()
                        time.sleep(0.01)

        # didn't find anything. let's wait.
        time.sleep(1)


def analyze_parfiles(conf, d):
    """ 
    Realtime analysis using newly found parameter files.
    """
    ch = conf.channel
    while True:

        ftry = get_next_chirp_par_file(conf, d)

        h = h5py.File(ftry, "r")
        t0 = float(np.copy(h[("t0")]))
        i0 = np.int64(t0*conf.sample_rate)
        chirp_rate = float(np.copy(h[("chirp_rate")]))
        h.close()

        # Spawn a separate process to copy the files off the ring buffer and place them
        # into the raw IQ staging directory
        copy_q = mp.Queue()
        move_q = mp.Queue()
        proc_copy = mp.Process(target=copy_data_files,
                               args=(conf, copy_q, move_q))
        proc_copy.start()
        proc_move = mp.Process(target=move_data_files, args=(conf, move_q))
        proc_move.start()

        chirp_downconvert(conf,
                          t0,
                          d,
                          i0,
                          conf.channel,
                          chirp_rate,
                          dec=conf.decimation,
                          cid=0,
                          copy_q=copy_q)

        # Indicate to the processes that we are done
        copy_q.put("")
        move_q.put("")
        proc_copy.join()
        proc_move.join()

        time.sleep(0.1)


if __name__ == "__main__":
    if len(sys.argv) == 2:
        conf = cc.chirp_config(sys.argv[1])
    else:
        conf = cc.chirp_config()

    # analyze serendpituous par files immediately after a chirp is detected
    if conf.serendipitous:
        # avoid having two processes snag the same sounder at the start
        time.sleep(rank * 2)
        while True:
            try:
                d = drf.DigitalRFReader(conf.data_dir)
                analyze_parfiles(conf, d)
            except:
                print("error in calc_ionograms.py. trying to restart")
                traceback.print_exc(file=sys.stdout)
                sys.stdout.flush()
                time.sleep(1)
    elif conf.realtime:  # analyze analytic timings
        while True:
            try:
                d = drf.DigitalRFReader(conf.data_dir)
                analyze_realtime(conf, d)
            except:
                print("error in calc_ionograms.py. trying to restart")
                sys.stdout.flush()
                time.sleep(1)
    else:  # batch analyze
        d = drf.DigitalRFReader(conf.data_dir)
        analyze_all(conf, d)
